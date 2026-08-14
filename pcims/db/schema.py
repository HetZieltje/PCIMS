"""Exact schema definition, creation, and semantic integrity checks."""

import sqlite3
from contextlib import closing
from datetime import date

from pcims.db.connection import Database
from pcims.db.errors import DatabaseIntegrityError, SchemaVersionError
from pcims.domain import ITEM_TYPES, MAX_NAME_LENGTH
from pcims.money import MAX_MONEY_CENTS

SCHEMA_VERSION = 13
_ALLOWED_TYPES_SQL = ",".join(f"'{item_type}'" for item_type in ITEM_TYPES)
_VALID_NAME_SQL = f"""length(trim(name)) BETWEEN 1 AND {MAX_NAME_LENGTH}
        AND instr(name,char(0))=0
        AND name NOT GLOB ('*[' || char(1) || '-' || char(31) || char(127) || ']*')"""
SCHEMA_DEFINITIONS: dict[tuple[str, str], str] = {
    ("table", "expenses"): f"""CREATE TABLE expenses (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL CHECK ({_VALID_NAME_SQL}),
        item_type TEXT NOT NULL CHECK (item_type IN ({_ALLOWED_TYPES_SQL})),
        price_cents INTEGER NOT NULL
            CHECK (price_cents >= 0 AND price_cents <= {MAX_MONEY_CENTS}),
        purchase_date TEXT NOT NULL
            CHECK (purchase_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
                   AND COALESCE(strftime('%Y-%m-%d',purchase_date)=purchase_date,0))
    ) STRICT""",
    ("table", "assembled_pcs"): f"""CREATE TABLE assembled_pcs (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL COLLATE PCIMS_NOCASE UNIQUE
            CHECK ({_VALID_NAME_SQL})
    ) STRICT""",
    ("table", "pc_parts"): """CREATE TABLE pc_parts (
        pc_id INTEGER NOT NULL REFERENCES assembled_pcs(id) ON DELETE CASCADE
            DEFERRABLE INITIALLY DEFERRED,
        expense_id INTEGER NOT NULL UNIQUE REFERENCES expenses(id) ON DELETE RESTRICT,
        position INTEGER NOT NULL CHECK (position >= 0),
        PRIMARY KEY (pc_id, expense_id),
        UNIQUE (pc_id, position)
    ) STRICT""",
    ("table", "sales"): f"""CREATE TABLE sales (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL CHECK ({_VALID_NAME_SQL}),
        kind TEXT NOT NULL CHECK (kind IN ('item', 'pc')),
        selling_price_cents INTEGER NOT NULL
            CHECK (selling_price_cents >= 0
                   AND selling_price_cents <= {MAX_MONEY_CENTS}),
        sale_date TEXT NOT NULL
            CHECK (sale_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
                   AND COALESCE(strftime('%Y-%m-%d',sale_date)=sale_date,0))
    ) STRICT""",
    ("table", "sale_items"): """CREATE TABLE sale_items (
        sale_id INTEGER NOT NULL REFERENCES sales(id) ON DELETE CASCADE
            DEFERRABLE INITIALLY DEFERRED,
        expense_id INTEGER NOT NULL UNIQUE REFERENCES expenses(id) ON DELETE RESTRICT,
        position INTEGER NOT NULL CHECK (position >= 0),
        PRIMARY KEY (sale_id, expense_id),
        UNIQUE (sale_id, position)
    ) STRICT""",
    (
        "index",
        "expenses_inventory_order",
    ): """CREATE INDEX expenses_inventory_order
        ON expenses(item_type,name COLLATE PCIMS_NOCASE,id)""",
    ("trigger", "pc_part_must_not_be_sold"): """CREATE TRIGGER pc_part_must_not_be_sold
        BEFORE INSERT ON pc_parts
        WHEN EXISTS (SELECT 1 FROM sale_items WHERE expense_id=NEW.expense_id)
        BEGIN
            SELECT RAISE(ABORT, 'sold expense cannot be assigned to a PC');
        END""",
    (
        "trigger",
        "pc_part_insert_requires_new_pc",
    ): """CREATE TRIGGER pc_part_insert_requires_new_pc
        BEFORE INSERT ON pc_parts
        WHEN EXISTS (SELECT 1 FROM assembled_pcs WHERE id=NEW.pc_id)
        BEGIN
            SELECT RAISE(ABORT, 'published PC membership is immutable');
        END""",
    (
        "trigger",
        "assembled_pc_requires_parts",
    ): """CREATE TRIGGER assembled_pc_requires_parts
        AFTER INSERT ON assembled_pcs
        WHEN NOT EXISTS (SELECT 1 FROM pc_parts WHERE pc_id=NEW.id)
        BEGIN
            SELECT RAISE(ABORT, 'assembled PC must contain components');
        END""",
    (
        "trigger",
        "sale_item_must_not_be_in_pc",
    ): """CREATE TRIGGER sale_item_must_not_be_in_pc
        BEFORE INSERT ON sale_items
        WHEN EXISTS (SELECT 1 FROM pc_parts WHERE expense_id=NEW.expense_id)
        BEGIN
            SELECT RAISE(ABORT, 'assembled expense cannot be sold separately');
        END""",
    (
        "trigger",
        "sale_item_insert_requires_new_sale",
    ): """CREATE TRIGGER sale_item_insert_requires_new_sale
        BEFORE INSERT ON sale_items
        WHEN EXISTS (SELECT 1 FROM sales WHERE id=NEW.sale_id)
        BEGIN
            SELECT RAISE(ABORT, 'published sale membership is immutable');
        END""",
    ("trigger", "pc_part_cost_limit"): """CREATE TRIGGER pc_part_cost_limit
        BEFORE INSERT ON pc_parts
        WHEN (SELECT price_cents FROM expenses WHERE id=NEW.expense_id)
             + COALESCE((SELECT SUM(e.price_cents)
                           FROM pc_parts pp
                           JOIN expenses e ON e.id=pp.expense_id
                          WHERE pp.pc_id=NEW.pc_id),0) > 99999999999
        BEGIN
            SELECT RAISE(ABORT, 'combined PC cost is too large');
        END""",
    (
        "trigger",
        "sale_item_cost_and_date_limit",
    ): """CREATE TRIGGER sale_item_cost_and_date_limit
        BEFORE INSERT ON sale_items
        WHEN EXISTS (
            SELECT 1 FROM expenses e JOIN sales s ON s.id=NEW.sale_id
             WHERE e.id=NEW.expense_id
               AND (e.purchase_date>s.sale_date
                    OR e.price_cents
                       + COALESCE((SELECT SUM(existing.price_cents)
                                     FROM sale_items si
                                     JOIN expenses existing
                                       ON existing.id=si.expense_id
                                    WHERE si.sale_id=NEW.sale_id),0)
                       > 99999999999)
        )
        BEGIN
            SELECT RAISE(ABORT, 'sale item has invalid cost or date');
        END""",
    (
        "trigger",
        "sale_requires_valid_items",
    ): """CREATE TRIGGER sale_requires_valid_items
        AFTER INSERT ON sales
        WHEN NOT EXISTS (SELECT 1 FROM sale_items WHERE sale_id=NEW.id)
          OR EXISTS (
              SELECT 1 FROM sale_items si JOIN expenses e ON e.id=si.expense_id
               WHERE si.sale_id=NEW.id AND e.purchase_date>NEW.sale_date
          )
          OR (SELECT SUM(e.price_cents)
                FROM sale_items si JOIN expenses e ON e.id=si.expense_id
               WHERE si.sale_id=NEW.id) > 99999999999
        BEGIN
            SELECT RAISE(ABORT, 'sale must contain valid items');
        END""",
    (
        "trigger",
        "linked_expense_value_is_immutable",
    ): """CREATE TRIGGER linked_expense_value_is_immutable
        BEFORE UPDATE OF price_cents,purchase_date ON expenses
        WHEN EXISTS (SELECT 1 FROM pc_parts WHERE expense_id=OLD.id)
          OR EXISTS (SELECT 1 FROM sale_items WHERE expense_id=OLD.id)
        BEGIN
            SELECT RAISE(ABORT, 'linked expense value is immutable');
        END""",
    (
        "trigger",
        "sold_expense_description_is_immutable",
    ): """CREATE TRIGGER sold_expense_description_is_immutable
        BEFORE UPDATE OF name,item_type ON expenses
        WHEN EXISTS (SELECT 1 FROM sale_items WHERE expense_id=OLD.id)
        BEGIN
            SELECT RAISE(ABORT, 'sold expense description is immutable');
        END""",
    ("trigger", "sale_record_is_immutable"): """CREATE TRIGGER sale_record_is_immutable
        BEFORE UPDATE ON sales
        BEGIN
            SELECT RAISE(ABORT, 'sale records are immutable');
        END""",
    ("trigger", "pc_part_is_immutable"): """CREATE TRIGGER pc_part_is_immutable
        BEFORE UPDATE ON pc_parts
        BEGIN
            SELECT RAISE(ABORT, 'PC membership rows are immutable');
        END""",
    (
        "trigger",
        "pc_part_delete_requires_pc_delete",
    ): """CREATE TRIGGER pc_part_delete_requires_pc_delete
        BEFORE DELETE ON pc_parts
        WHEN EXISTS (SELECT 1 FROM assembled_pcs WHERE id=OLD.pc_id)
        BEGIN
            SELECT RAISE(ABORT, 'disassemble by deleting the PC record');
        END""",
    ("trigger", "sale_item_is_immutable"): """CREATE TRIGGER sale_item_is_immutable
        BEFORE UPDATE ON sale_items
        BEGIN
            SELECT RAISE(ABORT, 'sale membership rows are immutable');
        END""",
    (
        "trigger",
        "sale_item_delete_requires_sale_delete",
    ): """CREATE TRIGGER sale_item_delete_requires_sale_delete
        BEFORE DELETE ON sale_items
        WHEN EXISTS (SELECT 1 FROM sales WHERE id=OLD.sale_id)
        BEGIN
            SELECT RAISE(ABORT, 'undo by deleting the sale record');
        END""",
}

for _money_trigger in (
    "pc_part_cost_limit",
    "sale_item_cost_and_date_limit",
    "sale_requires_valid_items",
):
    if str(MAX_MONEY_CENTS) not in SCHEMA_DEFINITIONS[("trigger", _money_trigger)]:
        raise RuntimeError(f"{_money_trigger} does not use the current money limit.")


def _normalize_schema_sql(sql: object) -> str:
    return " ".join(str(sql).split()).casefold()


def validate_schema(database: sqlite3.Connection) -> None:
    """Require the exact current tables, constraints, indexes, and triggers."""
    version = int(database.execute("PRAGMA user_version").fetchone()[0])
    actual = {
        (str(row[0]), str(row[1])): _normalize_schema_sql(row[2])
        for row in database.execute(
            """SELECT type,name,sql FROM sqlite_master
               WHERE name NOT LIKE 'sqlite_%'
                 AND type IN ('table','index','trigger','view')"""
        )
    }
    expected = {
        key: _normalize_schema_sql(sql) for key, sql in SCHEMA_DEFINITIONS.items()
    }
    if version == SCHEMA_VERSION and actual == expected:
        return

    missing = sorted(name for _, name in expected.keys() - actual.keys())
    unexpected = sorted(name for _, name in actual.keys() - expected.keys())
    changed = sorted(
        name
        for kind, name in expected.keys() & actual.keys()
        if expected[(kind, name)] != actual[(kind, name)]
    )
    differences: list[str] = []
    if version != SCHEMA_VERSION:
        differences.append(f"version {version}, expected {SCHEMA_VERSION}")
    if missing:
        differences.append(f"missing {', '.join(missing)}")
    if unexpected:
        differences.append(f"unexpected {', '.join(unexpected)}")
    if changed:
        differences.append(f"changed {', '.join(changed)}")
    raise SchemaVersionError(
        "Database schema is incompatible with the current format"
        f" ({'; '.join(differences)}). Restore a current-format backup or choose "
        "a new database."
    )


def _validate_dates(database: sqlite3.Connection) -> None:
    date_fields = (
        ("expenses", "purchase date", "SELECT id,purchase_date FROM expenses"),
        ("sales", "sale date", "SELECT id,sale_date FROM sales"),
    )
    for table, label, query in date_fields:
        for row_id, stored_date in database.execute(query):
            try:
                date.fromisoformat(stored_date)
            except (TypeError, ValueError) as exc:
                raise DatabaseIntegrityError(
                    f"Database contains an invalid {label} in {table} row {row_id}."
                ) from exc


def _validate_money(database: sqlite3.Connection) -> None:
    money_fields = (
        (
            "expenses",
            "SELECT id FROM expenses WHERE price_cents<0 OR price_cents>? LIMIT 1",
        ),
        (
            "sales",
            """SELECT id FROM sales
               WHERE selling_price_cents<0 OR selling_price_cents>? LIMIT 1""",
        ),
    )
    for table, query in money_fields:
        invalid = database.execute(query, (MAX_MONEY_CENTS,)).fetchone()
        if invalid:
            raise DatabaseIntegrityError(
                f"Database contains an invalid monetary value in {table} "
                f"row {invalid[0]}."
            )


def _validate_relationships(database: sqlite3.Connection) -> None:
    conflict = database.execute(
        """SELECT pp.expense_id FROM pc_parts pp
           JOIN sale_items si ON si.expense_id=pp.expense_id LIMIT 1"""
    ).fetchone()
    if conflict:
        raise DatabaseIntegrityError(
            f"Expense {conflict[0]} is both assigned to a PC and recorded as sold."
        )

    empty_pc = database.execute(
        """SELECT p.id FROM assembled_pcs p
           LEFT JOIN pc_parts pp ON pp.pc_id=p.id
           GROUP BY p.id HAVING COUNT(pp.expense_id)=0 LIMIT 1"""
    ).fetchone()
    if empty_pc:
        raise DatabaseIntegrityError(f"Assembled PC {empty_pc[0]} has no components.")

    seen_pc_names: dict[str, int] = {}
    for pc_id, pc_name in database.execute("SELECT id,name FROM assembled_pcs"):
        folded_name = pc_name.casefold()
        if folded_name in seen_pc_names:
            raise DatabaseIntegrityError(
                f"Assembled PCs {seen_pc_names[folded_name]} and {pc_id} have "
                "case-insensitively duplicate names."
            )
        seen_pc_names[folded_name] = pc_id

    invalid_sale = database.execute(
        """SELECT s.id FROM sales s
           LEFT JOIN sale_items si ON si.sale_id=s.id
           LEFT JOIN expenses e ON e.id=si.expense_id
           GROUP BY s.id
           HAVING COUNT(si.expense_id)=0
               OR COALESCE(SUM(e.price_cents),0)>?
               OR s.sale_date<MAX(e.purchase_date)
           LIMIT 1""",
        (MAX_MONEY_CENTS,),
    ).fetchone()
    if invalid_sale:
        raise DatabaseIntegrityError(
            f"Sale {invalid_sale[0]} has inconsistent items, cost, or dates."
        )


def validate_current_data(database: sqlite3.Connection) -> None:
    """Reject structurally valid databases with invalid business records."""
    _validate_dates(database)
    _validate_money(database)
    _validate_relationships(database)


def _initialize_database(database: Database) -> None:
    """Create the current schema, or reject any incompatible existing schema."""
    with closing(database.connect(create=True)) as setup_connection:
        journal_mode = setup_connection.execute("PRAGMA journal_mode = WAL").fetchone()[
            0
        ]
        if journal_mode.casefold() != "wal":
            raise DatabaseIntegrityError(
                f"Database could not enable WAL journaling (got {journal_mode})."
            )
    with database.transaction(write=True) as connection:
        objects_exist = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' LIMIT 1"
        ).fetchone()
        if objects_exist:
            validate_schema(connection)
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise DatabaseIntegrityError(
                    f"Database integrity check failed: {integrity}"
                )
            foreign_key_violations = connection.execute(
                "PRAGMA foreign_key_check"
            ).fetchall()
            if foreign_key_violations:
                table, row_id, referenced_table, _ = foreign_key_violations[0]
                raise DatabaseIntegrityError(
                    f"Database foreign-key check failed at {table} row {row_id} "
                    f"(missing {referenced_table} record)."
                )
            validate_current_data(connection)
            return

        for statement in SCHEMA_DEFINITIONS.values():
            connection.execute(statement)
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        validate_schema(connection)


def initialize_database(database: Database) -> None:
    """Initialize while excluding live operations and database replacement."""
    with database.gate.exclusive():
        _initialize_database(database)
