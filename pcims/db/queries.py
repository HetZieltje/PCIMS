"""Current-schema data access and atomic business workflows for PCIMS."""

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from pcims.db.connection import connection
from pcims.db.models import AssembledPC, Expense, FinancialSummary, Sale

SCHEMA_VERSION = 4
MAX_MONEY_CENTS = 99_999_999_999
ITEM_TYPES = (
    "CPU",
    "Cooler",
    "GPU",
    "Motherboard",
    "RAM",
    "SSD",
    "HDD",
    "Case",
    "PSU",
    "Fan",
    "Extra",
)
_ALLOWED_TYPES_SQL = ",".join(f"'{item_type}'" for item_type in ITEM_TYPES)
SCHEMA_DEFINITIONS = {
    ("table", "expenses"): f"""CREATE TABLE expenses (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL CHECK (length(trim(name)) > 0),
        item_type TEXT NOT NULL CHECK (item_type IN ({_ALLOWED_TYPES_SQL})),
        price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
        purchase_date TEXT NOT NULL
            CHECK (purchase_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]')
    )""",
    ("table", "assembled_pcs"): """CREATE TABLE assembled_pcs (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE CHECK (length(trim(name)) > 0)
    )""",
    ("table", "pc_parts"): """CREATE TABLE pc_parts (
        pc_id INTEGER NOT NULL REFERENCES assembled_pcs(id) ON DELETE CASCADE,
        expense_id INTEGER NOT NULL UNIQUE REFERENCES expenses(id) ON DELETE RESTRICT,
        position INTEGER NOT NULL CHECK (position >= 0),
        PRIMARY KEY (pc_id, expense_id),
        UNIQUE (pc_id, position)
    )""",
    ("table", "sales"): """CREATE TABLE sales (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL CHECK (length(trim(name)) > 0),
        kind TEXT NOT NULL CHECK (kind IN ('item', 'pc')),
        cost_cents INTEGER NOT NULL CHECK (cost_cents >= 0),
        selling_price_cents INTEGER NOT NULL CHECK (selling_price_cents >= 0),
        sale_date TEXT NOT NULL
            CHECK (sale_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]')
    )""",
    ("table", "sale_items"): """CREATE TABLE sale_items (
        sale_id INTEGER NOT NULL REFERENCES sales(id) ON DELETE CASCADE,
        expense_id INTEGER NOT NULL UNIQUE REFERENCES expenses(id) ON DELETE RESTRICT,
        position INTEGER NOT NULL CHECK (position >= 0),
        PRIMARY KEY (sale_id, expense_id),
        UNIQUE (sale_id, position)
    )""",
    ("trigger", "pc_part_must_not_be_sold"): """CREATE TRIGGER pc_part_must_not_be_sold
        BEFORE INSERT ON pc_parts
        WHEN EXISTS (SELECT 1 FROM sale_items WHERE expense_id=NEW.expense_id)
        BEGIN
            SELECT RAISE(ABORT, 'sold expense cannot be assigned to a PC');
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
}


class ValidationError(ValueError):
    pass


class NotFoundError(LookupError):
    pass


class SchemaVersionError(RuntimeError):
    pass


class DatabaseIntegrityError(RuntimeError):
    pass


def _normalize_schema_sql(sql):
    return " ".join(str(sql).split()).casefold()


def validate_schema(database):
    """Require the exact current tables, constraints, indexes, and triggers."""
    version = database.execute("PRAGMA user_version").fetchone()[0]
    actual = {
        (row[0], row[1]): _normalize_schema_sql(row[2])
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

    missing = sorted(name for key, name in expected.keys() - actual.keys())
    unexpected = sorted(name for key, name in actual.keys() - expected.keys())
    changed = sorted(
        name
        for key, name in expected.keys() & actual.keys()
        if expected[(key, name)] != actual[(key, name)]
    )
    differences = []
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


def _text(value, label):
    normalized = str(value).strip() if value is not None else ""
    if not normalized:
        raise ValidationError(f"{label} cannot be blank.")
    return normalized


def _item_type(value):
    normalized = _text(value, "Item type").casefold()
    for item_type in ITEM_TYPES:
        if item_type.casefold() == normalized:
            return item_type
    raise ValidationError(f"Item type must be one of: {', '.join(ITEM_TYPES)}.")


def _money_cents(value, label="Price"):
    try:
        amount = Decimal(str(value).replace("€", "").replace(",", ".").strip())
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError(f"{label} must be numeric.") from exc
    if not amount.is_finite() or amount < 0:
        raise ValidationError(f"{label} must be a finite, non-negative number.")
    if amount.as_tuple().exponent < -2:
        raise ValidationError(f"{label} can have at most two decimal places.")
    cents = int((amount * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    if cents > MAX_MONEY_CENTS:
        raise ValidationError(f"{label} is too large.")
    return cents


def _positive_id(value, label="ID"):
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{label} must be a positive integer.") from exc
    if parsed <= 0:
        raise ValidationError(f"{label} must be a positive integer.")
    return parsed


def _unique_ids(values, label):
    ids = [_positive_id(value, label) for value in values]
    if not ids:
        raise ValidationError(f"At least one {label.lower()} is required.")
    if len(ids) != len(set(ids)):
        raise ValidationError(f"Duplicate {label.lower()} values are not allowed.")
    return ids


def _iso_date(value):
    if value is None:
        return date.today().isoformat()
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.isoformat()
    try:
        return date.fromisoformat(str(value).strip()).isoformat()
    except (TypeError, ValueError) as exc:
        raise ValidationError("Date must use the YYYY-MM-DD format.") from exc


def validate_current_data(database):
    """Reject structurally valid databases with invalid business records."""
    for table, column in (("expenses", "purchase_date"), ("sales", "sale_date")):
        for row_id, stored_date in database.execute(f"SELECT id,{column} FROM {table}"):
            try:
                date.fromisoformat(stored_date)
            except (TypeError, ValueError) as exc:
                raise DatabaseIntegrityError(
                    f"Database contains an invalid {column.replace('_', ' ')} "
                    f"in {table} row {row_id}."
                ) from exc

    for table, columns in (
        ("expenses", ("price_cents",)),
        ("sales", ("cost_cents", "selling_price_cents")),
    ):
        for column in columns:
            invalid = database.execute(
                f"SELECT id FROM {table} WHERE {column}<0 OR {column}>? LIMIT 1",
                (MAX_MONEY_CENTS,),
            ).fetchone()
            if invalid:
                raise DatabaseIntegrityError(
                    f"Database contains an invalid monetary value in {table} "
                    f"row {invalid[0]}."
                )

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

    invalid_sale = database.execute(
        """SELECT s.id FROM sales s
           LEFT JOIN sale_items si ON si.sale_id=s.id
           LEFT JOIN expenses e ON e.id=si.expense_id
           GROUP BY s.id
           HAVING COUNT(si.expense_id)=0
               OR s.cost_cents<>COALESCE(SUM(e.price_cents),0)
               OR s.sale_date<MAX(e.purchase_date)
           LIMIT 1"""
    ).fetchone()
    if invalid_sale:
        raise DatabaseIntegrityError(
            f"Sale {invalid_sale[0]} has inconsistent items, cost, or dates."
        )


def initialize_database():
    """Create the current schema, or reject any incompatible existing schema."""
    with connection() as database:
        objects_exist = database.execute(
            "SELECT 1 FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' LIMIT 1"
        ).fetchone()
        if objects_exist:
            validate_schema(database)
            integrity = database.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise DatabaseIntegrityError(
                    f"Database integrity check failed: {integrity}"
                )
            foreign_key_violations = database.execute(
                "PRAGMA foreign_key_check"
            ).fetchall()
            if foreign_key_violations:
                table, row_id, referenced_table, _ = foreign_key_violations[0]
                raise DatabaseIntegrityError(
                    f"Database foreign-key check failed at {table} row {row_id} "
                    f"(missing {referenced_table} record)."
                )
            validate_current_data(database)
            return

        statements = ";\n".join(SCHEMA_DEFINITIONS.values())
        database.executescript(
            f"{statements};\nPRAGMA user_version = {SCHEMA_VERSION};"
        )
        validate_schema(database)


def _expense_from_row(row):
    return Expense(
        id=row["id"],
        name=row["name"],
        item_type=row["item_type"],
        price_cents=row["price_cents"],
        purchase_date=date.fromisoformat(row["purchase_date"]),
        pc_id=row["pc_id"],
        pc_name=row["pc_name"],
        sale_id=row["sale_id"],
    )


_EXPENSE_SELECT = """
    SELECT e.id,e.name,e.item_type,e.price_cents,e.purchase_date,
           p.id AS pc_id,p.name AS pc_name,si.sale_id
      FROM expenses e
      LEFT JOIN pc_parts pp ON pp.expense_id=e.id
      LEFT JOIN assembled_pcs p ON p.id=pp.pc_id
      LEFT JOIN sale_items si ON si.expense_id=e.id
"""


def add_expenses(items):
    """Record one or more purchased items atomically."""
    normalized = [
        (
            _text(item["name"], "Item name"),
            _item_type(item["item_type"]),
            _money_cents(item["price"], "Price"),
            _iso_date(item.get("purchase_date")),
        )
        for item in items
    ]
    if not normalized:
        raise ValidationError("At least one purchase item is required.")
    with connection() as database:
        return [
            database.execute(
                "INSERT INTO expenses (name,item_type,price_cents,purchase_date) VALUES (?,?,?,?)",
                item,
            ).lastrowid
            for item in normalized
        ]


def list_expenses():
    with connection() as database:
        rows = database.execute(_EXPENSE_SELECT + " ORDER BY e.id").fetchall()
    return tuple(_expense_from_row(row) for row in rows)


def list_inventory(item_type=None, available_only=False):
    clauses = ["si.sale_id IS NULL"]
    parameters = []
    if item_type is not None:
        clauses.append("e.item_type=?")
        parameters.append(_item_type(item_type))
    if available_only:
        clauses.append("p.id IS NULL")
    sql = (
        _EXPENSE_SELECT
        + " WHERE "
        + " AND ".join(clauses)
        + " ORDER BY e.item_type,e.name,e.id"
    )
    with connection() as database:
        rows = database.execute(sql, parameters).fetchall()
    return tuple(_expense_from_row(row) for row in rows)


def delete_expenses(expense_ids):
    ids = _unique_ids(expense_ids, "Expense ID")
    placeholders = ",".join("?" for _ in ids)
    with connection() as database:
        rows = database.execute(
            _EXPENSE_SELECT + f" WHERE e.id IN ({placeholders})", ids
        ).fetchall()
        if len(rows) != len(ids):
            found = {row["id"] for row in rows}
            missing = next(item_id for item_id in ids if item_id not in found)
            raise NotFoundError(f"Expense {missing} does not exist.")
        for row in rows:
            if row["pc_id"] is not None:
                raise ValidationError(
                    f"Expense {row['id']} belongs to PC '{row['pc_name']}'."
                )
            if row["sale_id"] is not None:
                raise ValidationError(
                    f"Expense {row['id']} has sale history. Undo the sale first."
                )
        database.executemany(
            "DELETE FROM expenses WHERE id=?", ((item_id,) for item_id in ids)
        )


def rename_expenses(expense_ids, new_name):
    ids = _unique_ids(expense_ids, "Expense ID")
    new_name = _text(new_name, "New item name")
    placeholders = ",".join("?" for _ in ids)
    with connection() as database:
        count = database.execute(
            f"SELECT COUNT(*) FROM expenses WHERE id IN ({placeholders})", ids
        ).fetchone()[0]
        if count != len(ids):
            raise NotFoundError("One or more selected expenses no longer exist.")
        database.execute(
            f"UPDATE expenses SET name=? WHERE id IN ({placeholders})", [new_name, *ids]
        )


def assemble_pc(name, expense_ids):
    name = _text(name, "PC name")
    ids = _unique_ids(expense_ids, "Expense ID")
    placeholders = ",".join("?" for _ in ids)
    with connection() as database:
        if database.execute(
            "SELECT 1 FROM assembled_pcs WHERE name=?", (name,)
        ).fetchone():
            raise ValidationError(f"A PC named '{name}' already exists.")
        rows = database.execute(
            _EXPENSE_SELECT + f" WHERE e.id IN ({placeholders})", ids
        ).fetchall()
        if len(rows) != len(ids):
            raise NotFoundError("One or more selected expenses no longer exist.")
        for row in rows:
            if row["pc_id"] is not None or row["sale_id"] is not None:
                raise ValidationError(f"'{row['name']}' is not available for assembly.")
        pc_id = database.execute(
            "INSERT INTO assembled_pcs (name) VALUES (?)", (name,)
        ).lastrowid
        database.executemany(
            "INSERT INTO pc_parts (pc_id,expense_id,position) VALUES (?,?,?)",
            ((pc_id, expense_id, position) for position, expense_id in enumerate(ids)),
        )
        return pc_id


def list_pcs():
    with connection() as database:
        pcs = database.execute(
            "SELECT id,name FROM assembled_pcs ORDER BY name,id"
        ).fetchall()
        rows = database.execute(
            _EXPENSE_SELECT + " WHERE p.id IS NOT NULL ORDER BY p.id,pp.position"
        ).fetchall()
    parts_by_pc = {pc["id"]: [] for pc in pcs}
    for row in rows:
        parts_by_pc[row["pc_id"]].append(_expense_from_row(row))
    return tuple(
        AssembledPC(pc["id"], pc["name"], tuple(parts_by_pc[pc["id"]])) for pc in pcs
    )


def disassemble_pc(pc_id):
    pc_id = _positive_id(pc_id, "PC ID")
    with connection() as database:
        result = database.execute("DELETE FROM assembled_pcs WHERE id=?", (pc_id,))
        if result.rowcount != 1:
            raise NotFoundError(f"PC {pc_id} does not exist.")


def rename_pc(pc_id, new_name):
    pc_id = _positive_id(pc_id, "PC ID")
    new_name = _text(new_name, "New PC name")
    with connection() as database:
        if database.execute(
            "SELECT 1 FROM assembled_pcs WHERE name=? AND id<>?", (new_name, pc_id)
        ).fetchone():
            raise ValidationError(f"A PC named '{new_name}' already exists.")
        result = database.execute(
            "UPDATE assembled_pcs SET name=? WHERE id=?", (new_name, pc_id)
        )
        if result.rowcount != 1:
            raise NotFoundError(f"PC {pc_id} does not exist.")


def _validate_sale_date(rows, sale_day):
    for row in rows:
        if sale_day < row["purchase_date"]:
            raise ValidationError(
                f"Sale date cannot be before purchase date {row['purchase_date']}."
            )


def sell_items(expense_ids, selling_price, sale_date=None):
    ids = _unique_ids(expense_ids, "Expense ID")
    selling_cents = _money_cents(selling_price, "Selling price")
    sale_day = _iso_date(sale_date)
    placeholders = ",".join("?" for _ in ids)
    with connection() as database:
        rows = database.execute(
            _EXPENSE_SELECT + f" WHERE e.id IN ({placeholders}) ORDER BY e.id", ids
        ).fetchall()
        if len(rows) != len(ids):
            raise NotFoundError("One or more selected expenses no longer exist.")
        for row in rows:
            if row["pc_id"] is not None or row["sale_id"] is not None:
                raise ValidationError(f"'{row['name']}' is not available for sale.")
        _validate_sale_date(rows, sale_day)
        names = {row["name"] for row in rows}
        name = rows[0]["name"] if len(names) == 1 else f"{len(rows)} items"
        cost_cents = sum(row["price_cents"] for row in rows)
        sale_id = database.execute(
            "INSERT INTO sales (name,kind,cost_cents,selling_price_cents,sale_date) VALUES (?,'item',?,?,?)",
            (name, cost_cents, selling_cents, sale_day),
        ).lastrowid
        database.executemany(
            "INSERT INTO sale_items (sale_id,expense_id,position) VALUES (?,?,?)",
            (
                (sale_id, expense_id, position)
                for position, expense_id in enumerate(ids)
            ),
        )
        return sale_id


def sell_pc(pc_id, selling_price, sale_date=None):
    pc_id = _positive_id(pc_id, "PC ID")
    selling_cents = _money_cents(selling_price, "Selling price")
    sale_day = _iso_date(sale_date)
    with connection() as database:
        pc = database.execute(
            "SELECT id,name FROM assembled_pcs WHERE id=?", (pc_id,)
        ).fetchone()
        if pc is None:
            raise NotFoundError(f"PC {pc_id} does not exist.")
        rows = database.execute(
            _EXPENSE_SELECT + " WHERE p.id=? ORDER BY pp.position", (pc_id,)
        ).fetchall()
        if not rows:
            raise ValidationError(f"PC '{pc['name']}' has no components.")
        _validate_sale_date(rows, sale_day)
        cost_cents = sum(row["price_cents"] for row in rows)
        expense_ids = [row["id"] for row in rows]
        database.execute("DELETE FROM assembled_pcs WHERE id=?", (pc_id,))
        sale_id = database.execute(
            "INSERT INTO sales (name,kind,cost_cents,selling_price_cents,sale_date) VALUES (?,'pc',?,?,?)",
            (pc["name"], cost_cents, selling_cents, sale_day),
        ).lastrowid
        database.executemany(
            "INSERT INTO sale_items (sale_id,expense_id,position) VALUES (?,?,?)",
            (
                (sale_id, expense_id, position)
                for position, expense_id in enumerate(expense_ids)
            ),
        )
        return sale_id


def list_sales():
    with connection() as database:
        sales = database.execute(
            "SELECT id,name,kind,cost_cents,selling_price_cents,sale_date FROM sales ORDER BY id"
        ).fetchall()
        rows = database.execute(
            _EXPENSE_SELECT
            + " WHERE si.sale_id IS NOT NULL ORDER BY si.sale_id,si.position"
        ).fetchall()
    items_by_sale = {sale["id"]: [] for sale in sales}
    for row in rows:
        items_by_sale[row["sale_id"]].append(_expense_from_row(row))
    return tuple(
        Sale(
            id=sale["id"],
            name=sale["name"],
            kind=sale["kind"],
            cost_cents=sale["cost_cents"],
            selling_price_cents=sale["selling_price_cents"],
            sale_date=date.fromisoformat(sale["sale_date"]),
            items=tuple(items_by_sale[sale["id"]]),
        )
        for sale in sales
    )


def undo_sale(sale_id):
    sale_id = _positive_id(sale_id, "Sale ID")
    with connection() as database:
        sale = database.execute(
            "SELECT id,name,kind FROM sales WHERE id=?", (sale_id,)
        ).fetchone()
        if sale is None:
            raise NotFoundError(f"Sale {sale_id} does not exist.")
        item_ids = [
            row[0]
            for row in database.execute(
                "SELECT expense_id FROM sale_items WHERE sale_id=? ORDER BY position",
                (sale_id,),
            )
        ]
        if not item_ids:
            raise ValidationError(f"Sale {sale_id} contains no recoverable items.")
        if sale["kind"] == "pc":
            if database.execute(
                "SELECT 1 FROM assembled_pcs WHERE name=?", (sale["name"],)
            ).fetchone():
                raise ValidationError(
                    f"Cannot undo while an assembled PC named '{sale['name']}' exists."
                )
            database.execute("DELETE FROM sales WHERE id=?", (sale_id,))
            pc_id = database.execute(
                "INSERT INTO assembled_pcs (name) VALUES (?)", (sale["name"],)
            ).lastrowid
            database.executemany(
                "INSERT INTO pc_parts (pc_id,expense_id,position) VALUES (?,?,?)",
                (
                    (pc_id, expense_id, position)
                    for position, expense_id in enumerate(item_ids)
                ),
            )
        else:
            database.execute("DELETE FROM sales WHERE id=?", (sale_id,))


def get_financial_summary():
    with connection() as database:
        expense_cents = database.execute(
            "SELECT COALESCE(SUM(price_cents),0) FROM expenses"
        ).fetchone()[0]
        income_cents, cost_cents = database.execute(
            "SELECT COALESCE(SUM(selling_price_cents),0),COALESCE(SUM(cost_cents),0) FROM sales"
        ).fetchone()
        inventory_cents = database.execute(
            """SELECT COALESCE(SUM(e.price_cents),0) FROM expenses e
               LEFT JOIN sale_items si ON si.expense_id=e.id
               WHERE si.sale_id IS NULL"""
        ).fetchone()[0]
    return FinancialSummary(
        expense_cents=expense_cents,
        income_cents=income_cents,
        profit_cents=income_cents - cost_cents,
        inventory_cents=inventory_cents,
    )
