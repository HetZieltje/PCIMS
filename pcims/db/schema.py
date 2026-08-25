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
from pcims.proofs import MAX_PROOF_BYTES, MAX_PROOFS_PER_ITEM, NewProof

SCHEMA_VERSION = 1
_ALLOWED_TYPES_SQL = ",".join(f"'{item_type}'" for item_type in ITEM_TYPES)
_ALLOWED_CONDITIONS_SQL = ",".join(f"'{condition}'" for condition in ITEM_CONDITIONS)
_VALID_NAME_SQL = f"""length(trim(name)) BETWEEN 1 AND {MAX_NAME_LENGTH}
        AND instr(name,char(0))=0
        AND name NOT GLOB ('*[' || char(1) || '-' || char(31) || char(127) || ']*')"""
_VALID_FILE_NAME_SQL = _VALID_NAME_SQL.replace("name", "file_name")

SCHEMA_DEFINITIONS: dict[tuple[str, str], str] = {
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


def _normalize_schema_sql(sql: object) -> str:
    return " ".join(str(sql).split()).casefold()


SCHEMA_CHECKSUM = hashlib.sha256(
    "\n".join(
        f"{kind}:{name}:{_normalize_schema_sql(sql)}"
        for (kind, name), sql in sorted(SCHEMA_DEFINITIONS.items())
    ).encode("utf-8")
).hexdigest()


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
    """Require the exact clean-baseline schema and its migration marker."""
    actual_version = int(database.execute("PRAGMA user_version").fetchone()[0])
    actual = _schema_objects(database)
    expected = {
        key: _normalize_schema_sql(sql) for key, sql in SCHEMA_DEFINITIONS.items()
    }
    if actual_version != SCHEMA_VERSION or actual != expected:
        missing = sorted(name for _, name in expected.keys() - actual.keys())
        unexpected = sorted(name for _, name in actual.keys() - expected.keys())
        changed = sorted(
            key[1]
            for key in expected.keys() & actual.keys()
            if expected[key] != actual[key]
        )
        differences = []
        if actual_version != SCHEMA_VERSION:
            differences.append(f"version {actual_version}, expected {SCHEMA_VERSION}")
        if missing:
            differences.append(f"missing {', '.join(missing)}")
        if unexpected:
            differences.append(f"unexpected {', '.join(unexpected)}")
        if changed:
            differences.append(f"changed {', '.join(changed)}")
        raise SchemaVersionError(
            "Database schema is incompatible with the clean pre-release baseline"
            f" ({'; '.join(differences)}). Choose a new database."
        )
    markers = database.execute(
        "SELECT version,name,checksum FROM schema_migrations ORDER BY version"
    ).fetchall()
    if len(markers) != 1 or tuple(markers[0]) != (
        SCHEMA_VERSION,
        "initial inventory baseline",
        SCHEMA_CHECKSUM,
    ):
        raise SchemaVersionError("Database baseline marker is missing or invalid.")


def _valid_iso_date(value: object) -> bool:
    try:
        date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return False
    return True


def validate_current_data(database: sqlite3.Connection) -> None:
    """Reject structurally valid databases with inconsistent inventory state."""
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
    invalid_item_sale = database.execute(
        """SELECT s.id FROM sales s JOIN sale_items si ON si.sale_id=s.id
           JOIN pc_parts pp ON pp.item_id=si.item_id WHERE s.kind='item' LIMIT 1"""
    ).fetchone()
    if invalid_item_sale:
        raise DatabaseIntegrityError(
            f"Item sale {invalid_item_sale[0]} contains a PC component."
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


def _inspect_database(database: Database) -> Literal["empty", "current"]:
    with database.transaction() as connection:
        if not _schema_objects(connection):
            return "empty"
        validate_schema(connection)
        _validate_storage(connection, thorough=False)
        validate_current_data(connection)
        return "current"


def _initialize_database(database: Database) -> None:
    with closing(database.connect(create=True)) as setup_connection:
        journal_mode = setup_connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        if str(journal_mode).casefold() != "wal":
            raise DatabaseIntegrityError(
                f"Database could not enable WAL journaling (got {journal_mode})."
            )
    if _inspect_database(database) == "current":
        return
    with database.transaction(write=True) as connection:
        for statement in SCHEMA_DEFINITIONS.values():
            connection.execute(statement)
        connection.execute(
            """INSERT INTO schema_migrations (version,name,checksum,applied_at)
               VALUES (?,?,?,strftime('%Y-%m-%dT%H:%M:%SZ','now'))""",
            (SCHEMA_VERSION, "initial inventory baseline", SCHEMA_CHECKSUM),
        )
        connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        validate_schema(connection)


def initialize_database(database: Database) -> None:
    """Create or validate the single supported pre-release database baseline."""
    with database.gate.maintenance(), database.gate.exclusive():
        _initialize_database(database)
