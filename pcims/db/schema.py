"""Clean baseline schema, creation, and semantic integrity checks."""

import hashlib
import sqlite3
from contextlib import closing
from datetime import date
from typing import Literal

from pcims.db.connection import Database
from pcims.db.errors import DatabaseIntegrityError, SchemaVersionError
from pcims.domain import ITEM_CONDITIONS, ITEM_TYPES, MAX_NAME_LENGTH, MAX_NOTES_LENGTH
from pcims.money import MAX_MONEY_CENTS
from pcims.proofs import (
    MAX_PROOF_BYTES,
    MAX_PROOFS_PER_ITEM,
    MAX_TOTAL_PROOF_BYTES,
    NewProof,
)

SCHEMA_VERSION = 3
_ALLOWED_TYPES_SQL = ",".join(f"'{item_type}'" for item_type in ITEM_TYPES)
_ALLOWED_CONDITIONS_SQL = ",".join(f"'{condition}'" for condition in ITEM_CONDITIONS)
_VALID_NAME_SQL = f"""length(trim(name)) BETWEEN 1 AND {MAX_NAME_LENGTH}
        AND instr(name,char(0))=0
        AND name NOT GLOB ('*[' || char(1) || '-' || char(31) || char(127) || ']*')"""
_VALID_FILE_NAME_SQL = _VALID_NAME_SQL.replace("name", "file_name")

SCHEMA_V1_DEFINITIONS: dict[tuple[str, str], str] = {
    ("table", "schema_migrations"): """CREATE TABLE schema_migrations (
        version INTEGER PRIMARY KEY CHECK (version>0),
        name TEXT NOT NULL UNIQUE,
        checksum TEXT NOT NULL CHECK (
            length(checksum)=64 AND checksum NOT GLOB '*[^0-9a-f]*'),
        applied_at TEXT NOT NULL
    ) STRICT""",
    ("table", "inventory_items"): f"""CREATE TABLE inventory_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL CHECK ({_VALID_NAME_SQL}),
        item_type TEXT NOT NULL CHECK (item_type IN ({_ALLOWED_TYPES_SQL})),
        price_cents INTEGER NOT NULL CHECK (price_cents BETWEEN 0 AND {MAX_MONEY_CENTS}),
        purchase_date TEXT NOT NULL
            CHECK (purchase_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
                   AND COALESCE(strftime('%Y-%m-%d',purchase_date)=purchase_date,0)),
        vendor TEXT NOT NULL DEFAULT ''
            CHECK (length(vendor)<={MAX_NAME_LENGTH} AND instr(vendor,char(0))=0),
        serial_number TEXT NOT NULL DEFAULT ''
            CHECK (length(serial_number)<={MAX_NAME_LENGTH} AND instr(serial_number,char(0))=0),
        storage_location TEXT NOT NULL DEFAULT ''
            CHECK (length(storage_location)<={MAX_NAME_LENGTH} AND instr(storage_location,char(0))=0),
        condition TEXT CHECK (condition IN ({_ALLOWED_CONDITIONS_SQL})),
        warranty_until TEXT CHECK (
            warranty_until IS NULL OR
            (warranty_until GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
             AND COALESCE(strftime('%Y-%m-%d',warranty_until)=warranty_until,0))),
        notes TEXT NOT NULL DEFAULT ''
            CHECK (length(notes)<={MAX_NOTES_LENGTH} AND instr(notes,char(0))=0)
    ) STRICT""",
    ("table", "pcs"): f"""CREATE TABLE pcs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL COLLATE PCIMS_NOCASE UNIQUE CHECK ({_VALID_NAME_SQL}),
        status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','sold'))
    ) STRICT""",
    ("table", "pc_parts"): """CREATE TABLE pc_parts (
        pc_id INTEGER NOT NULL REFERENCES pcs(id) ON DELETE CASCADE,
        item_id INTEGER NOT NULL UNIQUE REFERENCES inventory_items(id) ON DELETE RESTRICT,
        position INTEGER NOT NULL CHECK (position>=0),
        PRIMARY KEY (pc_id,item_id),
        UNIQUE (pc_id,position)
    ) STRICT""",
    ("table", "sales"): f"""CREATE TABLE sales (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL CHECK ({_VALID_NAME_SQL}),
        kind TEXT NOT NULL CHECK (kind IN ('item','pc')),
        pc_id INTEGER UNIQUE REFERENCES pcs(id) ON DELETE RESTRICT,
        selling_price_cents INTEGER NOT NULL
            CHECK (selling_price_cents BETWEEN 0 AND {MAX_MONEY_CENTS}),
        sale_date TEXT NOT NULL
            CHECK (sale_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
                   AND COALESCE(strftime('%Y-%m-%d',sale_date)=sale_date,0)),
        CHECK ((kind='pc' AND pc_id IS NOT NULL) OR (kind='item' AND pc_id IS NULL))
    ) STRICT""",
    ("table", "sale_items"): """CREATE TABLE sale_items (
        sale_id INTEGER NOT NULL REFERENCES sales(id) ON DELETE CASCADE,
        item_id INTEGER NOT NULL UNIQUE REFERENCES inventory_items(id) ON DELETE RESTRICT,
        position INTEGER NOT NULL CHECK (position>=0),
        PRIMARY KEY (sale_id,item_id),
        UNIQUE (sale_id,position)
    ) STRICT""",
    ("table", "proof_files"): f"""CREATE TABLE proof_files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        media_type TEXT NOT NULL CHECK (media_type IN
            ('application/pdf','image/png','image/jpeg','image/webp')),
        content BLOB NOT NULL CHECK (length(content) BETWEEN 1 AND {MAX_PROOF_BYTES}),
        sha256 TEXT NOT NULL UNIQUE CHECK (
            length(sha256)=64 AND sha256 NOT GLOB '*[^0-9a-f]*')
    ) STRICT""",
    ("table", "item_proofs"): f"""CREATE TABLE item_proofs (
        item_id INTEGER NOT NULL REFERENCES inventory_items(id) ON DELETE CASCADE,
        proof_id INTEGER NOT NULL REFERENCES proof_files(id) ON DELETE RESTRICT,
        file_name TEXT NOT NULL CHECK ({_VALID_FILE_NAME_SQL}),
        position INTEGER NOT NULL CHECK (position>=0),
        PRIMARY KEY (item_id,proof_id),
        UNIQUE (item_id,position)
    ) STRICT""",
    ("table", "activity_events"): """CREATE TABLE activity_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        occurred_at TEXT NOT NULL,
        action TEXT NOT NULL CHECK (length(trim(action)) BETWEEN 1 AND 200),
        entity_type TEXT NOT NULL CHECK (length(trim(entity_type)) BETWEEN 1 AND 200),
        entity_id INTEGER CHECK (entity_id IS NULL OR entity_id>0),
        summary TEXT NOT NULL CHECK (length(trim(summary)) BETWEEN 1 AND 1000)
    ) STRICT""",
    ("index", "inventory_items_order"): """CREATE INDEX inventory_items_order
        ON inventory_items(item_type,name COLLATE PCIMS_NOCASE,id)""",
    (
        "index",
        "item_proofs_by_file",
    ): """CREATE INDEX item_proofs_by_file ON item_proofs(proof_id)""",
    (
        "index",
        "activity_events_newest",
    ): """CREATE INDEX activity_events_newest ON activity_events(id DESC)""",
    (
        "trigger",
        "pc_part_item_must_be_available",
    ): """CREATE TRIGGER pc_part_item_must_be_available
        BEFORE INSERT ON pc_parts
        WHEN EXISTS (SELECT 1 FROM sale_items WHERE item_id=NEW.item_id)
        BEGIN SELECT RAISE(ABORT,'sold item cannot be assigned to a PC'); END""",
    (
        "trigger",
        "pc_part_target_must_be_active",
    ): """CREATE TRIGGER pc_part_target_must_be_active
        BEFORE INSERT ON pc_parts
        WHEN (SELECT status FROM pcs WHERE id=NEW.pc_id)<>'active'
        BEGIN SELECT RAISE(ABORT,'sold PC membership cannot be changed'); END""",
    (
        "trigger",
        "active_pc_part_delete_only",
    ): """CREATE TRIGGER active_pc_part_delete_only
        BEFORE DELETE ON pc_parts
        WHEN EXISTS (SELECT 1 FROM pcs WHERE id=OLD.pc_id AND status<>'active')
        BEGIN SELECT RAISE(ABORT,'sold PC membership cannot be changed'); END""",
    ("trigger", "sold_pc_name_is_locked"): """CREATE TRIGGER sold_pc_name_is_locked
        BEFORE UPDATE OF name ON pcs WHEN OLD.status='sold'
        BEGIN SELECT RAISE(ABORT,'sold PC cannot be edited'); END""",
    (
        "trigger",
        "sold_pc_cannot_be_deleted",
    ): """CREATE TRIGGER sold_pc_cannot_be_deleted
        BEFORE DELETE ON pcs WHEN OLD.status='sold'
        BEGIN SELECT RAISE(ABORT,'undo the PC sale before deleting the PC'); END""",
    (
        "trigger",
        "sale_item_assignment_valid",
    ): """CREATE TRIGGER sale_item_assignment_valid
        BEFORE INSERT ON sale_items
        WHEN NOT EXISTS (
            SELECT 1 FROM sales s WHERE s.id=NEW.sale_id AND (
                (s.kind='item' AND NOT EXISTS (
                    SELECT 1 FROM pc_parts WHERE item_id=NEW.item_id))
                OR (s.kind='pc' AND EXISTS (
                    SELECT 1 FROM pc_parts WHERE pc_id=s.pc_id AND item_id=NEW.item_id))
            )
        )
        BEGIN SELECT RAISE(ABORT,'item does not belong in this sale'); END""",
    ("trigger", "pc_sale_sets_status"): """CREATE TRIGGER pc_sale_sets_status
        AFTER INSERT ON sales WHEN NEW.kind='pc'
        BEGIN UPDATE pcs SET status='sold' WHERE id=NEW.pc_id; END""",
    (
        "trigger",
        "pc_sale_delete_restores_status",
    ): """CREATE TRIGGER pc_sale_delete_restores_status
        AFTER DELETE ON sales WHEN OLD.kind='pc'
        BEGIN UPDATE pcs SET status='active' WHERE id=OLD.pc_id; END""",
    (
        "trigger",
        "proof_link_delete_removes_orphan",
    ): """CREATE TRIGGER proof_link_delete_removes_orphan
        AFTER DELETE ON item_proofs
        WHEN NOT EXISTS (SELECT 1 FROM item_proofs WHERE proof_id=OLD.proof_id)
        BEGIN DELETE FROM proof_files WHERE id=OLD.proof_id; END""",
    ("trigger", "item_proof_count_limit"): f"""CREATE TRIGGER item_proof_count_limit
        BEFORE INSERT ON item_proofs
        WHEN (SELECT COUNT(*) FROM item_proofs WHERE item_id=NEW.item_id)>={MAX_PROOFS_PER_ITEM}
        BEGIN SELECT RAISE(ABORT,'too many proofs for item'); END""",  # nosec B608
    ("trigger", "item_proof_name_unique"): """CREATE TRIGGER item_proof_name_unique
        BEFORE INSERT ON item_proofs
        WHEN EXISTS (SELECT 1 FROM item_proofs WHERE item_id=NEW.item_id
                      AND file_name=NEW.file_name COLLATE PCIMS_NOCASE)
        BEGIN SELECT RAISE(ABORT,'duplicate proof file name for item'); END""",
}

SCHEMA_V2_DEFINITIONS = {
    key: sql
    for key, sql in SCHEMA_V1_DEFINITIONS.items()
    if key[1] not in {"activity_events", "activity_events_newest"}
}
SCHEMA_V2_DEFINITIONS[
    ("trigger", "proof_total_size_limit")
] = f"""CREATE TRIGGER proof_total_size_limit
        BEFORE INSERT ON proof_files
        WHEN COALESCE((SELECT SUM(length(content)) FROM proof_files),0)
             + length(NEW.content)>{MAX_TOTAL_PROOF_BYTES}
        BEGIN SELECT RAISE(ABORT,'stored proofs exceed 512 MiB total'); END"""  # nosec B608

SCHEMA_DEFINITIONS = dict(SCHEMA_V2_DEFINITIONS)
SCHEMA_DEFINITIONS.update(
    {
        ("table", "item_costs"): f"""CREATE TABLE item_costs (
            item_id INTEGER PRIMARY KEY REFERENCES inventory_items(id) ON DELETE CASCADE,
            cash_paid_cents INTEGER NOT NULL
                CHECK (cash_paid_cents BETWEEN 0 AND {MAX_MONEY_CENTS}),
            origin TEXT NOT NULL CHECK (origin IN ('purchase','extracted'))
        ) STRICT""",
        ("table", "laptops"): """CREATE TABLE laptops (
            item_id INTEGER PRIMARY KEY REFERENCES inventory_items(id) ON DELETE CASCADE
        ) STRICT""",
        ("table", "laptop_slots"): """CREATE TABLE laptop_slots (
            laptop_id INTEGER NOT NULL REFERENCES laptops(item_id) ON DELETE CASCADE,
            component_type TEXT NOT NULL CHECK (component_type IN ('RAM','SSD','HDD')),
            slot_number INTEGER NOT NULL CHECK (slot_number>0),
            extracted_item_id INTEGER NOT NULL UNIQUE
                REFERENCES inventory_items(id) ON DELETE RESTRICT,
            installed_item_id INTEGER UNIQUE
                REFERENCES inventory_items(id) ON DELETE RESTRICT,
            PRIMARY KEY (laptop_id,component_type,slot_number),
            CHECK (installed_item_id IS NULL OR installed_item_id<>extracted_item_id)
        ) STRICT""",
        ("table", "laptop_sales"): """CREATE TABLE laptop_sales (
            sale_id INTEGER PRIMARY KEY REFERENCES sales(id) ON DELETE CASCADE,
            laptop_id INTEGER NOT NULL UNIQUE REFERENCES laptops(item_id) ON DELETE RESTRICT
        ) STRICT""",
        (
            "trigger",
            "laptop_base_must_be_available",
        ): """CREATE TRIGGER laptop_base_must_be_available
            BEFORE INSERT ON laptops
            WHEN (SELECT item_type FROM inventory_items WHERE id=NEW.item_id)<>'Extra'
              OR EXISTS (SELECT 1 FROM pc_parts WHERE item_id=NEW.item_id)
              OR EXISTS (SELECT 1 FROM sale_items WHERE item_id=NEW.item_id)
              OR EXISTS (SELECT 1 FROM laptop_slots WHERE installed_item_id=NEW.item_id)
            BEGIN SELECT RAISE(ABORT,'laptop base must be an available Extra item'); END""",
        (
            "trigger",
            "laptop_slot_assignment_valid",
        ): """CREATE TRIGGER laptop_slot_assignment_valid
            BEFORE INSERT ON laptop_slots
            WHEN EXISTS (SELECT 1 FROM laptop_sales WHERE laptop_id=NEW.laptop_id)
              OR NOT EXISTS (
                  SELECT 1 FROM inventory_items e JOIN item_costs c ON c.item_id=e.id
                   WHERE e.id=NEW.extracted_item_id AND e.item_type=NEW.component_type
                     AND c.origin='extracted'
                     AND NOT EXISTS (SELECT 1 FROM pc_parts WHERE item_id=e.id)
                     AND NOT EXISTS (SELECT 1 FROM sale_items WHERE item_id=e.id))
              OR (NEW.installed_item_id IS NOT NULL AND NOT EXISTS (
                  SELECT 1 FROM inventory_items e
                   WHERE e.id=NEW.installed_item_id AND e.item_type=NEW.component_type
                     AND NOT EXISTS (SELECT 1 FROM laptops WHERE item_id=e.id)
                     AND NOT EXISTS (SELECT 1 FROM pc_parts WHERE item_id=e.id)
                     AND NOT EXISTS (SELECT 1 FROM sale_items WHERE item_id=e.id)
                     AND NOT EXISTS (SELECT 1 FROM laptop_slots WHERE installed_item_id=e.id)))
            BEGIN SELECT RAISE(ABORT,'invalid laptop component assignment'); END""",
        (
            "trigger",
            "laptop_slot_update_valid",
        ): """CREATE TRIGGER laptop_slot_update_valid
            BEFORE UPDATE ON laptop_slots
            WHEN EXISTS (SELECT 1 FROM laptop_sales WHERE laptop_id=OLD.laptop_id)
              OR NEW.laptop_id<>OLD.laptop_id
              OR NEW.component_type<>OLD.component_type
              OR NEW.slot_number<>OLD.slot_number
              OR NEW.extracted_item_id<>OLD.extracted_item_id
              OR (NEW.installed_item_id IS NOT NULL AND NOT EXISTS (
                  SELECT 1 FROM inventory_items e
                   WHERE e.id=NEW.installed_item_id AND e.item_type=NEW.component_type
                     AND NOT EXISTS (SELECT 1 FROM laptops WHERE item_id=e.id)
                     AND NOT EXISTS (SELECT 1 FROM pc_parts WHERE item_id=e.id)
                     AND NOT EXISTS (SELECT 1 FROM sale_items WHERE item_id=e.id)
                     AND NOT EXISTS (SELECT 1 FROM laptop_slots ls
                                      WHERE ls.installed_item_id=e.id
                                        AND NOT (ls.laptop_id=OLD.laptop_id
                                             AND ls.component_type=OLD.component_type
                                             AND ls.slot_number=OLD.slot_number))))
            BEGIN SELECT RAISE(ABORT,'invalid laptop component change'); END""",
        (
            "trigger",
            "sold_laptop_slots_are_locked",
        ): """CREATE TRIGGER sold_laptop_slots_are_locked
            BEFORE DELETE ON laptop_slots
            WHEN EXISTS (SELECT 1 FROM laptop_sales WHERE laptop_id=OLD.laptop_id)
            BEGIN SELECT RAISE(ABORT,'sold laptop components cannot be changed'); END""",
        (
            "trigger",
            "laptop_with_slots_cannot_be_deleted",
        ): """CREATE TRIGGER laptop_with_slots_cannot_be_deleted
            BEFORE DELETE ON laptops
            WHEN EXISTS (SELECT 1 FROM laptop_slots WHERE laptop_id=OLD.item_id)
            BEGIN SELECT RAISE(ABORT,'restore laptop components before deleting laptop'); END""",
        (
            "trigger",
            "laptop_base_type_is_locked",
        ): """CREATE TRIGGER laptop_base_type_is_locked
            BEFORE UPDATE OF item_type ON inventory_items
            WHEN EXISTS (SELECT 1 FROM laptops WHERE item_id=OLD.id)
                 AND NEW.item_type<>'Extra'
            BEGIN SELECT RAISE(ABORT,'laptop base type cannot be changed'); END""",
    }
)
SCHEMA_DEFINITIONS[
    ("trigger", "sale_item_assignment_valid")
] = """CREATE TRIGGER sale_item_assignment_valid
        BEFORE INSERT ON sale_items
        WHEN NOT EXISTS (
            SELECT 1 FROM sales s WHERE s.id=NEW.sale_id AND (
                (s.kind='item' AND NOT EXISTS (
                    SELECT 1 FROM laptop_sales WHERE sale_id=s.id)
                 AND NOT EXISTS (SELECT 1 FROM pc_parts WHERE item_id=NEW.item_id)
                 AND NOT EXISTS (SELECT 1 FROM laptops WHERE item_id=NEW.item_id)
                 AND NOT EXISTS (SELECT 1 FROM laptop_slots WHERE installed_item_id=NEW.item_id))
                OR (s.kind='pc' AND EXISTS (
                    SELECT 1 FROM pc_parts WHERE pc_id=s.pc_id AND item_id=NEW.item_id))
                OR (s.kind='item' AND EXISTS (
                    SELECT 1 FROM laptop_sales ls WHERE ls.sale_id=s.id AND (
                        ls.laptop_id=NEW.item_id OR EXISTS (
                            SELECT 1 FROM laptop_slots lslot
                             WHERE lslot.laptop_id=ls.laptop_id
                               AND lslot.installed_item_id=NEW.item_id))))
            )
        )
        BEGIN SELECT RAISE(ABORT,'item does not belong in this sale'); END"""


def _normalize_schema_sql(sql: object) -> str:
    return " ".join(str(sql).split()).casefold()


def _schema_checksum(definitions: dict[tuple[str, str], str]) -> str:
    return hashlib.sha256(
        "\n".join(
            f"{kind}:{name}:{_normalize_schema_sql(sql)}"
            for (kind, name), sql in sorted(definitions.items())
        ).encode("utf-8")
    ).hexdigest()


SCHEMA_V1_CHECKSUM = _schema_checksum(SCHEMA_V1_DEFINITIONS)
SCHEMA_V2_CHECKSUM = _schema_checksum(SCHEMA_V2_DEFINITIONS)
SCHEMA_CHECKSUM = _schema_checksum(SCHEMA_DEFINITIONS)
SCHEMA_REVISIONS = {
    1: (
        "initial inventory baseline",
        SCHEMA_V1_CHECKSUM,
        SCHEMA_V1_DEFINITIONS,
    ),
    2: (
        "streamline history and cap proof storage",
        SCHEMA_V2_CHECKSUM,
        SCHEMA_V2_DEFINITIONS,
    ),
    3: (
        "add optional laptop inventory and split cash from cost basis",
        SCHEMA_CHECKSUM,
        SCHEMA_DEFINITIONS,
    ),
}


def _schema_objects(database: sqlite3.Connection) -> dict[tuple[str, str], str]:
    return {
        (str(row[0]), str(row[1])): _normalize_schema_sql(row[2])
        for row in database.execute(
            """SELECT type,name,sql FROM sqlite_master
               WHERE name NOT LIKE 'sqlite_%'
                 AND type IN ('table','index','trigger','view')"""
        )
    }


def validate_schema(database: sqlite3.Connection) -> None:
    """Require an exact known schema revision and its complete migration history."""
    actual_version = int(database.execute("PRAGMA user_version").fetchone()[0])
    revision = SCHEMA_REVISIONS.get(actual_version)
    if revision is None:
        raise SchemaVersionError(
            "Database schema is incompatible with this PCIMS version "
            f"(version {actual_version}, supported 1 through {SCHEMA_VERSION})."
        )
    _name, _checksum, definitions = revision
    actual = _schema_objects(database)
    expected = {key: _normalize_schema_sql(sql) for key, sql in definitions.items()}
    if actual != expected:
        missing = sorted(name for _, name in expected.keys() - actual.keys())
        unexpected = sorted(name for _, name in actual.keys() - expected.keys())
        changed = sorted(
            key[1]
            for key in expected.keys() & actual.keys()
            if expected[key] != actual[key]
        )
        differences = []
        if missing:
            differences.append(f"missing {', '.join(missing)}")
        if unexpected:
            differences.append(f"unexpected {', '.join(unexpected)}")
        if changed:
            differences.append(f"changed {', '.join(changed)}")
        raise SchemaVersionError(
            f"Database schema version {actual_version} is incompatible"
            f" ({'; '.join(differences)})."
        )
    markers = database.execute(
        "SELECT version,name,checksum FROM schema_migrations ORDER BY version"
    ).fetchall()
    expected_markers = tuple(
        (version, SCHEMA_REVISIONS[version][0], SCHEMA_REVISIONS[version][1])
        for version in range(1, actual_version + 1)
    )
    if tuple(tuple(row) for row in markers) != expected_markers:
        raise SchemaVersionError("Database migration history is missing or invalid.")


def _valid_iso_date(value: object) -> bool:
    try:
        date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return False
    return True


def validate_current_data(database: sqlite3.Connection) -> None:
    """Reject structurally valid databases with inconsistent inventory state."""
    has_laptop_schema = int(database.execute("PRAGMA user_version").fetchone()[0]) >= 3
    for item_id, purchase_date, warranty_until in database.execute(
        "SELECT id,purchase_date,warranty_until FROM inventory_items"
    ):
        if not _valid_iso_date(purchase_date):
            raise DatabaseIntegrityError(
                f"Item {item_id} has an invalid purchase date."
            )
        if warranty_until is not None and not _valid_iso_date(warranty_until):
            raise DatabaseIntegrityError(
                f"Item {item_id} has an invalid warranty date."
            )
    for sale_id, sale_date in database.execute("SELECT id,sale_date FROM sales"):
        if not _valid_iso_date(sale_date):
            raise DatabaseIntegrityError(f"Sale {sale_id} has an invalid sale date.")

    if has_laptop_schema:
        missing_cost = database.execute(
            """SELECT e.id FROM inventory_items e LEFT JOIN item_costs c ON c.item_id=e.id
               WHERE c.item_id IS NULL LIMIT 1"""
        ).fetchone()
        if missing_cost:
            raise DatabaseIntegrityError(f"Item {missing_cost[0]} has no cost record.")
        invalid_extracted = database.execute(
            """SELECT c.item_id FROM item_costs c
               LEFT JOIN laptop_slots ls ON ls.extracted_item_id=c.item_id
               WHERE c.origin='extracted'
                 AND (c.cash_paid_cents<>0 OR ls.laptop_id IS NULL) LIMIT 1"""
        ).fetchone()
        if invalid_extracted:
            raise DatabaseIntegrityError(
                f"Extracted item {invalid_extracted[0]} has invalid provenance."
            )
        invalid_laptop_value = database.execute(
            """SELECT l.item_id FROM laptops l
               JOIN inventory_items base ON base.id=l.item_id
               JOIN item_costs c ON c.item_id=l.item_id
               LEFT JOIN laptop_slots ls ON ls.laptop_id=l.item_id
               LEFT JOIN inventory_items extracted ON extracted.id=ls.extracted_item_id
               GROUP BY l.item_id
               HAVING base.item_type<>'Extra' OR c.origin<>'purchase'
                  OR base.price_cents+COALESCE(SUM(extracted.price_cents),0)
                     <>c.cash_paid_cents LIMIT 1"""
        ).fetchone()
        if invalid_laptop_value:
            raise DatabaseIntegrityError(
                f"Laptop {invalid_laptop_value[0]} has an invalid transferred value."
            )

    empty_pc = database.execute(
        """SELECT p.id FROM pcs p LEFT JOIN pc_parts pp ON pp.pc_id=p.id
           GROUP BY p.id HAVING COUNT(pp.item_id)=0 LIMIT 1"""
    ).fetchone()
    if empty_pc:
        raise DatabaseIntegrityError(f"PC {empty_pc[0]} has no components.")
    invalid_pc_state = database.execute(
        """SELECT p.id FROM pcs p LEFT JOIN sales s ON s.pc_id=p.id
           GROUP BY p.id HAVING (p.status='sold')<>(COUNT(s.id)=1) LIMIT 1"""
    ).fetchone()
    if invalid_pc_state:
        raise DatabaseIntegrityError(
            f"PC {invalid_pc_state[0]} has an invalid sale state."
        )
    invalid_sale = database.execute(
        """SELECT s.id FROM sales s
           LEFT JOIN sale_items si ON si.sale_id=s.id
           LEFT JOIN inventory_items i ON i.id=si.item_id
           GROUP BY s.id
           HAVING COUNT(si.item_id)=0 OR COALESCE(SUM(i.price_cents),0)>?
               OR s.sale_date<MAX(i.purchase_date) LIMIT 1""",
        (MAX_MONEY_CENTS,),
    ).fetchone()
    if invalid_sale:
        raise DatabaseIntegrityError(
            f"Sale {invalid_sale[0]} has invalid items, cost, or dates."
        )
    if has_laptop_schema:
        invalid_item_sale = database.execute(
            """SELECT s.id FROM sales s JOIN sale_items si ON si.sale_id=s.id
               JOIN pc_parts pp ON pp.item_id=si.item_id WHERE s.kind='item'
                 AND NOT EXISTS (
                     SELECT 1 FROM laptop_sales ls WHERE ls.sale_id=s.id)
               LIMIT 1"""
        ).fetchone()
    else:
        invalid_item_sale = database.execute(
            """SELECT s.id FROM sales s JOIN sale_items si ON si.sale_id=s.id
               JOIN pc_parts pp ON pp.item_id=si.item_id
               WHERE s.kind='item' LIMIT 1"""
        ).fetchone()
    if invalid_item_sale:
        raise DatabaseIntegrityError(
            f"Item sale {invalid_item_sale[0]} contains a PC component."
        )
    if has_laptop_schema:
        invalid_plain_item_sale = database.execute(
            """SELECT s.id FROM sales s JOIN sale_items si ON si.sale_id=s.id
               LEFT JOIN laptop_sales sale_laptop ON sale_laptop.sale_id=s.id
               LEFT JOIN laptops base ON base.item_id=si.item_id
               LEFT JOIN laptop_slots installed ON installed.installed_item_id=si.item_id
               WHERE s.kind='item' AND sale_laptop.sale_id IS NULL
                 AND (base.item_id IS NOT NULL OR installed.laptop_id IS NOT NULL)
               LIMIT 1"""
        ).fetchone()
        if invalid_plain_item_sale:
            raise DatabaseIntegrityError(
                f"Item sale {invalid_plain_item_sale[0]} contains a laptop or installed part."
            )
    invalid_pc_sale = database.execute(
        """SELECT s.id FROM sales s WHERE s.kind='pc' AND (
             EXISTS (SELECT item_id FROM pc_parts WHERE pc_id=s.pc_id
                     EXCEPT SELECT item_id FROM sale_items WHERE sale_id=s.id)
             OR EXISTS (SELECT item_id FROM sale_items WHERE sale_id=s.id
                        EXCEPT SELECT item_id FROM pc_parts WHERE pc_id=s.pc_id)
           ) LIMIT 1"""
    ).fetchone()
    if invalid_pc_sale:
        raise DatabaseIntegrityError(
            f"PC sale {invalid_pc_sale[0]} does not match its PC components."
        )
    invalid_laptop_sale = (
        database.execute(
            """SELECT ls.sale_id FROM laptop_sales ls WHERE
             EXISTS (
               SELECT laptop_id AS item_id FROM laptop_sales WHERE sale_id=ls.sale_id
               UNION SELECT installed_item_id FROM laptop_slots
                      WHERE laptop_id=ls.laptop_id AND installed_item_id IS NOT NULL
               EXCEPT SELECT item_id FROM sale_items WHERE sale_id=ls.sale_id)
             OR EXISTS (
               SELECT item_id FROM sale_items WHERE sale_id=ls.sale_id
               EXCEPT SELECT laptop_id FROM laptop_sales WHERE sale_id=ls.sale_id
               EXCEPT SELECT installed_item_id FROM laptop_slots
                      WHERE laptop_id=ls.laptop_id AND installed_item_id IS NOT NULL)
           LIMIT 1"""
        ).fetchone()
        if has_laptop_schema
        else None
    )
    if invalid_laptop_sale is not None:
        raise DatabaseIntegrityError(
            f"Laptop sale {invalid_laptop_sale[0]} does not match its tracked contents."
        )
    orphan = database.execute(
        """SELECT pf.id FROM proof_files pf LEFT JOIN item_proofs ip ON ip.proof_id=pf.id
           WHERE ip.proof_id IS NULL LIMIT 1"""
    ).fetchone()
    if orphan:
        raise DatabaseIntegrityError(
            f"Proof file {orphan[0]} is not attached to an item."
        )


def validate_proof_files(database: sqlite3.Connection) -> None:
    """Verify each stored blob, hash, signature, and per-item filename."""
    for (
        proof_id,
        file_name,
        media_type,
        stored_content,
        stored_hash,
    ) in database.execute(
        """SELECT pf.id,ip.file_name,pf.media_type,pf.content,pf.sha256
           FROM proof_files pf JOIN item_proofs ip ON ip.proof_id=pf.id"""
    ):
        content = bytes(stored_content)
        try:
            NewProof(file_name, media_type, content)
        except (TypeError, ValueError) as error:
            raise DatabaseIntegrityError(
                f"Proof file {proof_id} has invalid metadata or content."
            ) from error
        if hashlib.sha256(content).hexdigest() != stored_hash:
            raise DatabaseIntegrityError(
                f"Proof file {proof_id} failed its content hash check."
            )


def _validate_storage(database: sqlite3.Connection, *, thorough: bool = True) -> None:
    check = "PRAGMA integrity_check" if thorough else "PRAGMA quick_check"
    result = database.execute(check).fetchone()[0]
    if result != "ok":
        raise DatabaseIntegrityError(f"Database integrity check failed: {result}")
    violation = database.execute("PRAGMA foreign_key_check").fetchone()
    if violation:
        raise DatabaseIntegrityError(
            f"Database foreign-key check failed at {violation[0]} row {violation[1]}."
        )


def _inspect_database(database: Database) -> Literal["empty"] | int:
    with database.transaction() as connection:
        if not _schema_objects(connection):
            return "empty"
        validate_schema(connection)
        _validate_storage(connection, thorough=False)
        validate_current_data(connection)
        return int(connection.execute("PRAGMA user_version").fetchone()[0])


def _record_revision(connection: sqlite3.Connection, version: int) -> None:
    name, checksum, _definitions = SCHEMA_REVISIONS[version]
    connection.execute(
        """INSERT INTO schema_migrations (version,name,checksum,applied_at)
           VALUES (?,?,?,strftime('%Y-%m-%dT%H:%M:%SZ','now'))""",
        (version, name, checksum),
    )
    connection.execute(f"PRAGMA user_version={version}")


def _upgrade_schema(connection: sqlite3.Connection, version: int) -> None:
    while version < SCHEMA_VERSION:
        if version == 1:
            connection.execute("DROP TABLE activity_events")
            connection.execute(
                SCHEMA_V2_DEFINITIONS[("trigger", "proof_total_size_limit")]
            )
        elif version == 2:
            for table in ("item_costs", "laptops", "laptop_slots", "laptop_sales"):
                connection.execute(SCHEMA_DEFINITIONS[("table", table)])
                if table == "item_costs":
                    connection.execute(
                        """INSERT INTO item_costs (item_id,cash_paid_cents,origin)
                           SELECT id,price_cents,'purchase' FROM inventory_items"""
                    )
            connection.execute("DROP TRIGGER sale_item_assignment_valid")
            connection.execute(
                SCHEMA_DEFINITIONS[("trigger", "sale_item_assignment_valid")]
            )
            for trigger in (
                "laptop_base_must_be_available",
                "laptop_slot_assignment_valid",
                "laptop_slot_update_valid",
                "sold_laptop_slots_are_locked",
                "laptop_with_slots_cannot_be_deleted",
                "laptop_base_type_is_locked",
            ):
                connection.execute(SCHEMA_DEFINITIONS[("trigger", trigger)])
        else:  # pragma: no cover - every supported source has a registered step
            raise SchemaVersionError(
                f"No database migration is registered after version {version}."
            )
        version += 1
        _record_revision(connection, version)


def _initialize_database(database: Database) -> None:
    with closing(database.connect(create=True)) as setup_connection:
        journal_mode = setup_connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        if str(journal_mode).casefold() != "wal":
            raise DatabaseIntegrityError(
                f"Database could not enable WAL journaling (got {journal_mode})."
            )
    state = _inspect_database(database)
    if state == SCHEMA_VERSION:
        return
    if state == "empty":
        with database.transaction(write=True) as connection:
            for statement in SCHEMA_V1_DEFINITIONS.values():
                connection.execute(statement)
            _record_revision(connection, 1)
            _upgrade_schema(connection, 1)
            validate_schema(connection)
        return

    # A verified snapshot is mandatory before modifying a recognized older revision.
    from pcims.db.backup import create_backup

    create_backup(database=database)
    with database.transaction(write=True) as connection:
        _upgrade_schema(connection, state)
        validate_schema(connection)
        _validate_storage(connection, thorough=False)
        validate_current_data(connection)


def initialize_database(database: Database) -> None:
    """Create, migrate, or validate the database from the clean v1 baseline."""
    with database.gate.maintenance(), database.gate.exclusive():
        _initialize_database(database)
