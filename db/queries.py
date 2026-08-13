"""Current-schema data access and atomic business workflows for PCIMS."""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from db.connection import connection
from db.models import AssembledPC, Expense, FinancialSummary, Sale


SCHEMA_VERSION = 3
ITEM_TYPES = (
    "CPU", "Cooler", "GPU", "Motherboard", "RAM", "SSD", "HDD",
    "Case", "PSU", "Fan", "Extra",
)
REQUIRED_TABLES = {
    "expenses", "assembled_pcs", "pc_parts", "sales", "sale_items",
}
SCHEMA_COLUMNS = {
    "expenses": ("id", "name", "item_type", "price_cents", "purchase_date"),
    "assembled_pcs": ("id", "name"),
    "pc_parts": ("pc_id", "expense_id", "position"),
    "sales": ("id", "name", "kind", "cost_cents", "selling_price_cents", "sale_date"),
    "sale_items": ("sale_id", "expense_id", "position"),
}


class ValidationError(ValueError):
    pass


class NotFoundError(LookupError):
    pass


class SchemaVersionError(RuntimeError):
    pass


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
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


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


def initialize_database():
    """Create the current schema, or reject any incompatible existing schema."""
    with connection() as database:
        tables = {
            row[0] for row in database.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        version = database.execute("PRAGMA user_version").fetchone()[0]
        if tables:
            columns_match = REQUIRED_TABLES.issubset(tables) and all(
                tuple(row[1] for row in database.execute(f'PRAGMA table_info("{table}")'))
                == expected
                for table, expected in SCHEMA_COLUMNS.items()
            )
            if version != SCHEMA_VERSION or not columns_match:
                raise SchemaVersionError(
                    f"Database schema {version} is incompatible with required schema "
                    f"{SCHEMA_VERSION}. Restore a current-format backup or choose a new database."
                )
            return

        allowed_types = ",".join(f"'{item_type}'" for item_type in ITEM_TYPES)
        database.executescript(
            f"""
            CREATE TABLE expenses (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL CHECK (length(trim(name)) > 0),
                item_type TEXT NOT NULL CHECK (item_type IN ({allowed_types})),
                price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
                purchase_date TEXT NOT NULL
                    CHECK (purchase_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]')
            );

            CREATE TABLE assembled_pcs (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE CHECK (length(trim(name)) > 0)
            );

            CREATE TABLE pc_parts (
                pc_id INTEGER NOT NULL REFERENCES assembled_pcs(id) ON DELETE CASCADE,
                expense_id INTEGER NOT NULL UNIQUE REFERENCES expenses(id) ON DELETE RESTRICT,
                position INTEGER NOT NULL CHECK (position >= 0),
                PRIMARY KEY (pc_id, expense_id)
            );

            CREATE TABLE sales (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL CHECK (length(trim(name)) > 0),
                kind TEXT NOT NULL CHECK (kind IN ('item', 'pc')),
                cost_cents INTEGER NOT NULL CHECK (cost_cents >= 0),
                selling_price_cents INTEGER NOT NULL CHECK (selling_price_cents >= 0),
                sale_date TEXT NOT NULL
                    CHECK (sale_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]')
            );

            CREATE TABLE sale_items (
                sale_id INTEGER NOT NULL REFERENCES sales(id) ON DELETE CASCADE,
                expense_id INTEGER NOT NULL UNIQUE REFERENCES expenses(id) ON DELETE RESTRICT,
                position INTEGER NOT NULL CHECK (position >= 0),
                PRIMARY KEY (sale_id, expense_id)
            );

            CREATE TRIGGER pc_part_must_not_be_sold
            BEFORE INSERT ON pc_parts
            WHEN EXISTS (SELECT 1 FROM sale_items WHERE expense_id=NEW.expense_id)
            BEGIN
                SELECT RAISE(ABORT, 'sold expense cannot be assigned to a PC');
            END;

            CREATE TRIGGER sale_item_must_not_be_in_pc
            BEFORE INSERT ON sale_items
            WHEN EXISTS (SELECT 1 FROM pc_parts WHERE expense_id=NEW.expense_id)
            BEGIN
                SELECT RAISE(ABORT, 'assembled expense cannot be sold separately');
            END;

            PRAGMA user_version = {SCHEMA_VERSION};
            """
        )


def _expense_from_row(row):
    return Expense(
        id=row["id"],
        name=row["name"],
        item_type=row["item_type"],
        price_cents=row["price_cents"],
        purchase_date=date.fromisoformat(row["purchase_date"]),
        pc_id=row["pc_id"] if "pc_id" in row.keys() else None,
        pc_name=row["pc_name"] if "pc_name" in row.keys() else None,
        sale_id=row["sale_id"] if "sale_id" in row.keys() else None,
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
    sql = _EXPENSE_SELECT + " WHERE " + " AND ".join(clauses) + " ORDER BY e.item_type,e.name,e.id"
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
                raise ValidationError(f"Expense {row['id']} belongs to PC '{row['pc_name']}'.")
            if row["sale_id"] is not None:
                raise ValidationError(f"Expense {row['id']} has sale history. Undo the sale first.")
        database.executemany("DELETE FROM expenses WHERE id=?", ((item_id,) for item_id in ids))


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
        if database.execute("SELECT 1 FROM assembled_pcs WHERE name=?", (name,)).fetchone():
            raise ValidationError(f"A PC named '{name}' already exists.")
        rows = database.execute(
            _EXPENSE_SELECT + f" WHERE e.id IN ({placeholders})", ids
        ).fetchall()
        if len(rows) != len(ids):
            raise NotFoundError("One or more selected expenses no longer exist.")
        for row in rows:
            if row["pc_id"] is not None or row["sale_id"] is not None:
                raise ValidationError(f"'{row['name']}' is not available for assembly.")
        pc_id = database.execute("INSERT INTO assembled_pcs (name) VALUES (?)", (name,)).lastrowid
        database.executemany(
            "INSERT INTO pc_parts (pc_id,expense_id,position) VALUES (?,?,?)",
            ((pc_id, expense_id, position) for position, expense_id in enumerate(ids)),
        )
        return pc_id


def list_pcs():
    with connection() as database:
        pcs = database.execute("SELECT id,name FROM assembled_pcs ORDER BY name,id").fetchall()
        result = []
        for pc in pcs:
            rows = database.execute(
                _EXPENSE_SELECT + " WHERE p.id=? ORDER BY pp.position", (pc["id"],)
            ).fetchall()
            result.append(AssembledPC(pc["id"], pc["name"], tuple(_expense_from_row(row) for row in rows)))
    return tuple(result)


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
        result = database.execute("UPDATE assembled_pcs SET name=? WHERE id=?", (new_name, pc_id))
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
            ((sale_id, expense_id, position) for position, expense_id in enumerate(ids)),
        )
        return sale_id


def sell_pc(pc_id, selling_price, sale_date=None):
    pc_id = _positive_id(pc_id, "PC ID")
    selling_cents = _money_cents(selling_price, "Selling price")
    sale_day = _iso_date(sale_date)
    with connection() as database:
        pc = database.execute("SELECT id,name FROM assembled_pcs WHERE id=?", (pc_id,)).fetchone()
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
            ((sale_id, expense_id, position) for position, expense_id in enumerate(expense_ids)),
        )
        return sale_id


def list_sales():
    with connection() as database:
        sales = database.execute(
            "SELECT id,name,kind,cost_cents,selling_price_cents,sale_date FROM sales ORDER BY id"
        ).fetchall()
        result = []
        for sale in sales:
            rows = database.execute(
                _EXPENSE_SELECT + " WHERE si.sale_id=? ORDER BY si.position", (sale["id"],)
            ).fetchall()
            result.append(Sale(
                id=sale["id"], name=sale["name"], kind=sale["kind"],
                cost_cents=sale["cost_cents"], selling_price_cents=sale["selling_price_cents"],
                sale_date=date.fromisoformat(sale["sale_date"]),
                items=tuple(_expense_from_row(row) for row in rows),
            ))
    return tuple(result)


def undo_sale(sale_id):
    sale_id = _positive_id(sale_id, "Sale ID")
    with connection() as database:
        sale = database.execute("SELECT id,name,kind FROM sales WHERE id=?", (sale_id,)).fetchone()
        if sale is None:
            raise NotFoundError(f"Sale {sale_id} does not exist.")
        item_ids = [
            row[0] for row in database.execute(
                "SELECT expense_id FROM sale_items WHERE sale_id=? ORDER BY position", (sale_id,)
            )
        ]
        if not item_ids:
            raise ValidationError(f"Sale {sale_id} contains no recoverable items.")
        if sale["kind"] == "pc":
            if database.execute("SELECT 1 FROM assembled_pcs WHERE name=?", (sale["name"],)).fetchone():
                raise ValidationError(
                    f"Cannot undo while an assembled PC named '{sale['name']}' exists."
                )
            database.execute("DELETE FROM sales WHERE id=?", (sale_id,))
            pc_id = database.execute(
                "INSERT INTO assembled_pcs (name) VALUES (?)", (sale["name"],)
            ).lastrowid
            database.executemany(
                "INSERT INTO pc_parts (pc_id,expense_id,position) VALUES (?,?,?)",
                ((pc_id, expense_id, position) for position, expense_id in enumerate(item_ids)),
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
