"""Database queries and atomic business operations for PCIMS."""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from db.connection import connection


ITEM_TYPES = (
    "CPU", "Cooler", "GPU", "Motherboard", "RAM", "SSD", "HDD",
    "Case", "PSU", "Fan", "Extra",
)
COMPONENT_COLUMNS = {
    "CPU": "cpu",
    "Cooler": "cooler",
    "GPU": "gpu",
    "Motherboard": "motherboard",
    "RAM": "ram",
    "SSD": "ssd",
    "HDD": "hdd",
    "Case": "pc_case",
    "PSU": "psu",
    "Fan": "fan",
    "Extra": "extra",
}
COMPONENT_ORDER = tuple(COMPONENT_COLUMNS)


class ValidationError(ValueError):
    """Raised when user input violates a PCIMS business rule."""


class NotFoundError(LookupError):
    """Raised when a requested record does not exist."""


def _text(value, label):
    value = str(value).strip() if value is not None else ""
    if not value:
        raise ValidationError(f"{label} cannot be blank.")
    return value


def _item_name(value, label="Item name"):
    value = _text(value, label)
    if ";" in value:
        raise ValidationError(f"{label} cannot contain a semicolon.")
    return value


def _money_cents(value, label="Price"):
    try:
        value = Decimal(str(value).replace("€", "").replace("$", "").replace(",", ".").strip())
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError(f"{label} must be a number.") from exc
    if not value.is_finite() or value < 0:
        raise ValidationError(f"{label} must be a finite, non-negative number.")
    return int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _money(value, label="Price"):
    return _money_cents(value, label) / 100


def _positive_id(value, label="ID"):
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{label} must be a positive integer.") from exc
    if parsed <= 0:
        raise ValidationError(f"{label} must be a positive integer.")
    return parsed


def _iso_date(value):
    if value is None or value == "CURRENT_DATE":
        return date.today().isoformat()
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.isoformat()
    try:
        return date.fromisoformat(str(value).strip()).isoformat()
    except (TypeError, ValueError) as exc:
        raise ValidationError("Date must be a valid date in YYYY-MM-DD format.") from exc


def _item_type(value):
    normalized = _text(value, "Item type").casefold()
    for item_type in ITEM_TYPES:
        if item_type.casefold() == normalized:
            return item_type
    raise ValidationError(f"Item type must be one of: {', '.join(ITEM_TYPES)}.")


def initialize_database():
    """Create or safely extend the current database schema."""
    with connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                type TEXT NOT NULL CHECK (type IN
                    ('CPU','Cooler','GPU','Motherboard','RAM','SSD','HDD','Case','PSU','Fan','Extra')),
                price REAL NOT NULL CHECK (price >= 0),
                price_cents INTEGER CHECK (price_cents >= 0),
                purchase_date TEXT NOT NULL DEFAULT CURRENT_DATE,
                in_inventory INTEGER NOT NULL DEFAULT 1 CHECK (in_inventory IN (0, 1)),
                used_in TEXT
            );

            CREATE TABLE IF NOT EXISTS assembled_pcs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                price REAL NOT NULL CHECK (price >= 0),
                price_cents INTEGER CHECK (price_cents >= 0),
                cpu TEXT, cooler TEXT, gpu TEXT, motherboard TEXT, ram TEXT,
                ssd TEXT, hdd TEXT, pc_case TEXT, psu TEXT, fan TEXT, extra TEXT
            );

            CREATE TABLE IF NOT EXISTS assembled_pc_parts (
                pc_id INTEGER NOT NULL REFERENCES assembled_pcs(id) ON DELETE CASCADE,
                expense_id INTEGER NOT NULL UNIQUE REFERENCES expenses(id),
                component_type TEXT NOT NULL,
                position INTEGER NOT NULL,
                PRIMARY KEY (pc_id, expense_id)
            );

            CREATE TABLE IF NOT EXISTS income (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                old_id INTEGER,
                name TEXT NOT NULL,
                cost REAL NOT NULL CHECK (cost >= 0),
                selling_price REAL NOT NULL CHECK (selling_price >= 0),
                profit REAL NOT NULL,
                cost_cents INTEGER CHECK (cost_cents >= 0),
                selling_price_cents INTEGER CHECK (selling_price_cents >= 0),
                profit_cents INTEGER,
                sale_date TEXT NOT NULL DEFAULT CURRENT_DATE,
                is_pc INTEGER NOT NULL DEFAULT 0 CHECK (is_pc IN (0, 1))
            );

            CREATE TABLE IF NOT EXISTS sold_pcs (
                id INTEGER PRIMARY KEY REFERENCES income(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                cpu INTEGER REFERENCES expenses(id),
                cooler INTEGER REFERENCES expenses(id),
                gpu INTEGER REFERENCES expenses(id),
                motherboard INTEGER REFERENCES expenses(id),
                ram INTEGER REFERENCES expenses(id),
                ssd INTEGER REFERENCES expenses(id),
                hdd INTEGER REFERENCES expenses(id),
                pc_case INTEGER REFERENCES expenses(id),
                psu INTEGER REFERENCES expenses(id),
                fan INTEGER REFERENCES expenses(id),
                extra INTEGER REFERENCES expenses(id)
            );

            CREATE TABLE IF NOT EXISTS sold_pc_parts (
                sale_id INTEGER NOT NULL REFERENCES income(id) ON DELETE CASCADE,
                expense_id INTEGER NOT NULL REFERENCES expenses(id),
                component_type TEXT NOT NULL,
                position INTEGER NOT NULL,
                PRIMARY KEY (sale_id, expense_id)
            );
            """
        )

        migration_columns = {
            "expenses": {"price_cents": "INTEGER"},
            "assembled_pcs": {"price_cents": "INTEGER"},
            "income": {
                "cost_cents": "INTEGER",
                "selling_price_cents": "INTEGER",
                "profit_cents": "INTEGER",
            },
        }
        for table, columns in migration_columns.items():
            existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            for column, definition in columns.items():
                if column not in existing:
                    conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {definition}')

        conn.execute(
            "UPDATE expenses SET price_cents=CAST(ROUND(price*100) AS INTEGER) WHERE price_cents IS NULL"
        )
        conn.execute(
            "UPDATE assembled_pcs SET price_cents=CAST(ROUND(price*100) AS INTEGER) WHERE price_cents IS NULL"
        )
        conn.execute(
            """UPDATE income SET
                   cost_cents=COALESCE(cost_cents,CAST(ROUND(cost*100) AS INTEGER)),
                   selling_price_cents=COALESCE(selling_price_cents,CAST(ROUND(selling_price*100) AS INTEGER)),
                   profit_cents=COALESCE(profit_cents,CAST(ROUND(profit*100) AS INTEGER))"""
        )

        for pc_id, pc_name in conn.execute("SELECT id,name FROM assembled_pcs ORDER BY id"):
            parts = conn.execute(
                "SELECT id,type FROM expenses WHERE used_in=? ORDER BY id", (pc_name,)
            ).fetchall()
            for position, (expense_id, component_type) in enumerate(parts):
                conn.execute(
                    """INSERT OR IGNORE INTO assembled_pc_parts
                       (pc_id,expense_id,component_type,position) VALUES (?,?,?,?)""",
                    (pc_id, expense_id, component_type, position),
                )

        # Older databases allowed duplicate PC names. Add the index only when safe.
        duplicate = conn.execute(
            "SELECT name FROM assembled_pcs GROUP BY name HAVING COUNT(*) > 1 LIMIT 1"
        ).fetchone()
        if duplicate is None:
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_assembled_pcs_name ON assembled_pcs(name)"
            )
        conn.execute("PRAGMA user_version = 2")


# Inventory queries
def add_item_to_inventory(item_id):
    item_id = _positive_id(item_id, "Item ID")
    with connection() as conn:
        result = conn.execute("UPDATE expenses SET in_inventory = 1 WHERE id = ?", (item_id,))
        if result.rowcount != 1:
            raise NotFoundError(f"Item {item_id} does not exist.")


def get_inventory_items():
    with connection() as conn:
        rows = conn.execute(
            """SELECT id,name,type,COALESCE(price_cents,CAST(ROUND(price*100) AS INTEGER)),used_in
               FROM expenses WHERE in_inventory=1 ORDER BY id"""
        ).fetchall()
    return [(row[0], row[1], row[2], row[3] / 100, row[4]) for row in rows]


def delete_item_from_inventory(item_id):
    item_id = _positive_id(item_id, "Item ID")
    with connection() as conn:
        result = conn.execute("UPDATE expenses SET in_inventory = 0 WHERE id = ?", (item_id,))
        if result.rowcount != 1:
            raise NotFoundError(f"Item {item_id} does not exist.")


def delete_components_used_in_pc(pc_name):
    with connection() as conn:
        conn.execute("UPDATE expenses SET in_inventory = 0 WHERE used_in = ?", (_text(pc_name, "PC name"),))


def update_used_in_component(pc_name, names, item_type):
    """Legacy single-step assignment; prefer assemble_inventory_pc()."""
    pc_name = _text(pc_name, "PC name")
    item_type = _item_type(item_type)
    with connection() as conn:
        for name in filter(None, (part.strip() for part in str(names).split(";"))):
            row = conn.execute(
                """SELECT id FROM expenses
                   WHERE name=? AND type=? AND used_in IS NULL AND in_inventory=1
                   ORDER BY id LIMIT 1""",
                (name, item_type),
            ).fetchone()
            if row is None:
                raise NotFoundError(f"No available {item_type} named '{name}'.")
            conn.execute("UPDATE expenses SET used_in=? WHERE id=?", (pc_name, row[0]))


def update_used_in_for_deleted_pc(pc_name):
    with connection() as conn:
        conn.execute("UPDATE expenses SET used_in=NULL WHERE used_in=?", (_text(pc_name, "PC name"),))


def get_total_pc_price(pc_name):
    pc_name = _text(pc_name, "PC name")
    with connection() as conn:
        row = conn.execute(
            """SELECT SUM(COALESCE(e.price_cents,CAST(ROUND(e.price*100) AS INTEGER)))
               FROM assembled_pc_parts app
               JOIN assembled_pcs pc ON pc.id=app.pc_id
               JOIN expenses e ON e.id=app.expense_id WHERE pc.name=?""",
            (pc_name,),
        ).fetchone()
        if row[0] is None:
            row = conn.execute(
                """SELECT COALESCE(SUM(COALESCE(price_cents,CAST(ROUND(price*100) AS INTEGER))),0)
                   FROM expenses WHERE used_in=?""",
                (pc_name,),
            ).fetchone()
        return row[0] / 100


# Expense queries
def add_expense(name, item_type, price, purchase_date=None):
    price_cents = _money_cents(price)
    with connection() as conn:
        cursor = conn.execute(
            "INSERT INTO expenses (name,type,price,price_cents,purchase_date) VALUES (?,?,?,?,?)",
            (_item_name(name), _item_type(item_type), price_cents / 100,
             price_cents, _iso_date(purchase_date)),
        )
        return cursor.lastrowid


def add_expenses(items):
    """Add a purchase bundle atomically and return its expense IDs."""
    normalized = [
        (_item_name(item["name"]), _item_type(item["component_type"]), cents / 100,
         cents, _iso_date(item.get("purchase_date")))
        for item in items
        for cents in [_money_cents(item["price"])]
    ]
    if not normalized:
        raise ValidationError("At least one purchase item is required.")
    ids = []
    with connection() as conn:
        for item in normalized:
            ids.append(conn.execute(
                "INSERT INTO expenses (name,type,price,price_cents,purchase_date) VALUES (?,?,?,?,?)", item
            ).lastrowid)
    return ids


def _ensure_expense_deletable(conn, item_id):
    row = conn.execute("SELECT used_in FROM expenses WHERE id=?", (item_id,)).fetchone()
    if row is None:
        raise NotFoundError(f"Expense {item_id} does not exist.")
    if row[0] is not None:
        raise ValidationError(f"Expense {item_id} is assigned to PC '{row[0]}'.")
    sold = conn.execute(
        """SELECT 1 FROM income WHERE old_id=?
           UNION ALL SELECT 1 FROM sold_pc_parts WHERE expense_id=? LIMIT 1""",
        (item_id, item_id),
    ).fetchone()
    if sold:
        raise ValidationError(
            f"Expense {item_id} has sale history. Undo the sale before deleting it."
        )


def delete_expense(item_id):
    item_id = _positive_id(item_id, "Expense ID")
    with connection() as conn:
        _ensure_expense_deletable(conn, item_id)
        result = conn.execute("DELETE FROM expenses WHERE id=?", (item_id,))
        return result.rowcount == 1


def delete_expenses(item_ids):
    """Delete a selected group atomically."""
    ids = [_positive_id(item_id, "Expense ID") for item_id in item_ids]
    if not ids:
        raise ValidationError("At least one expense must be selected.")
    with connection() as conn:
        for item_id in ids:
            _ensure_expense_deletable(conn, item_id)
        conn.executemany("DELETE FROM expenses WHERE id=?", ((item_id,) for item_id in ids))


def get_expenses():
    with connection() as conn:
        rows = conn.execute(
            """SELECT id,name,type,COALESCE(price_cents,CAST(ROUND(price*100) AS INTEGER)),
                      purchase_date,in_inventory,used_in FROM expenses ORDER BY id"""
        ).fetchall()
    return [
        {"id": row[0], "name": row[1], "type": row[2], "price": row[3] / 100,
         "purchase_date": row[4], "in_inventory": bool(row[5]), "used_in": row[6]}
        for row in rows
    ]


def get_inventory_value():
    with connection() as conn:
        cents = conn.execute(
            """SELECT COALESCE(SUM(COALESCE(price_cents,CAST(ROUND(price*100) AS INTEGER))),0)
               FROM expenses WHERE in_inventory=1"""
        ).fetchone()[0]
        return cents / 100


def get_purchase_date(item_id):
    with connection() as conn:
        row = conn.execute("SELECT purchase_date FROM expenses WHERE id=?", (_positive_id(item_id, "Item ID"),)).fetchone()
        return row[0] if row else None


def get_item_cost(item_id):
    with connection() as conn:
        row = conn.execute(
            "SELECT COALESCE(price_cents,CAST(ROUND(price*100) AS INTEGER)) FROM expenses WHERE id=?",
            (_positive_id(item_id, "Item ID"),),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"Item {item_id} does not exist.")
        return row[0] / 100


# Assembled PC queries and workflows
def assemble_pc(pc_name, price, components):
    """Insert an already-reserved PC record (legacy API)."""
    pc_name = _text(pc_name, "PC name")
    values = [str(components.get(key, components.get(key.lower(), ""))) for key in COMPONENT_ORDER]
    price_cents = _money_cents(price)
    with connection() as conn:
        conn.execute(
            """INSERT INTO assembled_pcs
               (name,price,price_cents,cpu,cooler,gpu,motherboard,ram,ssd,hdd,pc_case,psu,fan,extra)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (pc_name, price_cents / 100, price_cents, *values),
        )


def assemble_inventory_pc(pc_name, components):
    """Reserve selected inventory parts and create the PC in one transaction."""
    pc_name = _text(pc_name, "PC name")
    selected = {kind: [n.strip() for n in str(components.get(kind, "")).split(";") if n.strip()]
                for kind in COMPONENT_ORDER}
    if not any(selected.values()):
        raise ValidationError("At least one component must be selected.")

    with connection() as conn:
        if conn.execute("SELECT 1 FROM assembled_pcs WHERE name=?", (pc_name,)).fetchone():
            raise ValidationError(f"A PC named '{pc_name}' already exists.")
        total_cents = 0
        reserved_parts = []
        for kind, names in selected.items():
            for name in names:
                row = conn.execute(
                    """SELECT id,COALESCE(price_cents,CAST(ROUND(price*100) AS INTEGER)) FROM expenses
                       WHERE name=? AND type=? AND in_inventory=1 AND used_in IS NULL
                       ORDER BY id LIMIT 1""",
                    (name, kind),
                ).fetchone()
                if row is None:
                    raise NotFoundError(f"No available {kind} named '{name}'.")
                conn.execute("UPDATE expenses SET used_in=? WHERE id=?", (pc_name, row[0]))
                total_cents += row[1]
                reserved_parts.append((row[0], kind))
        ordered = [";".join(selected[kind]) for kind in COMPONENT_ORDER]
        cursor = conn.execute(
            """INSERT INTO assembled_pcs
               (name,price,price_cents,cpu,cooler,gpu,motherboard,ram,ssd,hdd,pc_case,psu,fan,extra)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (pc_name, total_cents / 100, total_cents, *ordered),
        )
        pc_id = cursor.lastrowid
        conn.executemany(
            """INSERT INTO assembled_pc_parts (pc_id,expense_id,component_type,position)
               VALUES (?,?,?,?)""",
            ((pc_id, expense_id, kind, position)
             for position, (expense_id, kind) in enumerate(reserved_parts)),
        )
        return total_cents / 100


def get_assembled_pcs():
    with connection() as conn:
        rows = conn.execute(
            """SELECT id,name,COALESCE(price_cents,CAST(ROUND(price*100) AS INTEGER))/100.0,
                      cpu,cooler,gpu,motherboard,ram,ssd,hdd,pc_case,psu,fan,extra
               FROM assembled_pcs ORDER BY id"""
        ).fetchall()
        result = []
        for row in rows:
            part_rows = conn.execute(
                """SELECT e.name,e.type FROM assembled_pc_parts app
                   JOIN expenses e ON e.id=app.expense_id
                   WHERE app.pc_id=? ORDER BY app.position""",
                (row[0],),
            ).fetchall()
            if part_rows:
                names = {kind: [] for kind in COMPONENT_ORDER}
                for part_name, kind in part_rows:
                    names[kind].append(part_name)
                result.append((row[0], row[1], row[2], *[";".join(names[kind]) for kind in COMPONENT_ORDER]))
            else:
                result.append(row)
        return result


def delete_assembled_pc(pc_name):
    pc_name = _text(pc_name, "PC name")
    with connection() as conn:
        part_ids = [row[0] for row in conn.execute(
            """SELECT app.expense_id FROM assembled_pc_parts app
               JOIN assembled_pcs pc ON pc.id=app.pc_id WHERE pc.name=?""",
            (pc_name,),
        )]
        result = conn.execute("DELETE FROM assembled_pcs WHERE name=?", (pc_name,))
        if result.rowcount:
            conn.execute("UPDATE expenses SET used_in=NULL WHERE used_in=?", (pc_name,))
            conn.executemany("UPDATE expenses SET used_in=NULL WHERE id=?", ((item_id,) for item_id in part_ids))
        return result.rowcount == 1


def get_pc_names():
    with connection() as conn:
        return [row[0] for row in conn.execute("SELECT name FROM assembled_pcs ORDER BY name")]


# Sale queries and workflows
def add_income(old_id, name, cost, selling_price, profit, sale_date=None, is_pc=False):
    cost_cents = _money_cents(cost, "Cost")
    selling_cents = _money_cents(selling_price, "Selling price")
    try:
        profit_cents = int(
            (Decimal(str(profit)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        )
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError("Profit must be a number.") from exc
    with connection() as conn:
        cursor = conn.execute(
            """INSERT INTO income
               (old_id,name,cost,selling_price,profit,cost_cents,selling_price_cents,
                profit_cents,sale_date,is_pc)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (old_id, _text(name, "Sale name"), cost_cents / 100, selling_cents / 100,
             profit_cents / 100, cost_cents, selling_cents, profit_cents,
             _iso_date(sale_date), int(bool(is_pc))),
        )
        return cursor.lastrowid


def sell_inventory_items(item_ids, total_selling_price, sale_date):
    """Sell standalone inventory items atomically and return income IDs."""
    ids = [_positive_id(item_id, "Item ID") for item_id in item_ids]
    if not ids:
        raise ValidationError("At least one item must be selected.")
    sale_day = _iso_date(sale_date)
    total_cents = _money_cents(total_selling_price, "Selling price")
    base_cents, remainder = divmod(total_cents, len(ids))
    allocated_cents = [
        base_cents + (1 if index < remainder else 0)
        for index in range(len(ids))
    ]
    income_ids = []
    with connection() as conn:
        for item_id, item_cents in zip(ids, allocated_cents):
            item_price = item_cents / 100
            row = conn.execute(
                """SELECT name,COALESCE(price_cents,CAST(ROUND(price*100) AS INTEGER)),
                          purchase_date,used_in,in_inventory FROM expenses WHERE id=?""",
                (item_id,),
            ).fetchone()
            if row is None:
                raise NotFoundError(f"Item {item_id} does not exist.")
            name, cost_cents, purchased, used_in, in_inventory = row
            if not in_inventory or used_in is not None:
                raise ValidationError(f"'{name}' is not available for sale.")
            if sale_day < _iso_date(purchased):
                raise ValidationError(f"Sale date cannot be before purchase date {purchased}.")
            cursor = conn.execute(
                """INSERT INTO income
                   (old_id,name,cost,selling_price,profit,cost_cents,selling_price_cents,
                    profit_cents,sale_date,is_pc)
                   VALUES (?,?,?,?,?,?,?,?,?,0)""",
                (item_id, name, cost_cents / 100, item_price,
                 (item_cents - cost_cents) / 100, cost_cents,
                 item_cents, item_cents - cost_cents,
                 sale_day),
            )
            income_ids.append(cursor.lastrowid)
            conn.execute("UPDATE expenses SET in_inventory=0 WHERE id=?", (item_id,))
    return income_ids


def sell_assembled_pc(pc_name, selling_price, sale_date):
    """Sell a PC and all of its parts atomically."""
    pc_name = _text(pc_name, "PC name")
    selling_cents = _money_cents(selling_price, "Selling price")
    selling_price = selling_cents / 100
    sale_day = _iso_date(sale_date)
    with connection() as conn:
        pc = conn.execute("SELECT id FROM assembled_pcs WHERE name=?", (pc_name,)).fetchone()
        if pc is None:
            raise NotFoundError(f"PC '{pc_name}' does not exist.")
        pc_id = pc[0]
        parts = conn.execute(
            """SELECT e.id,e.name,e.type,
                      COALESCE(e.price_cents,CAST(ROUND(e.price*100) AS INTEGER)),
                      e.purchase_date
               FROM assembled_pc_parts app
               JOIN expenses e ON e.id=app.expense_id
               WHERE app.pc_id=? AND e.in_inventory=1 ORDER BY app.position""",
            (pc_id,),
        ).fetchall()
        if not parts:
            parts = conn.execute(
                """SELECT id,name,type,COALESCE(price_cents,CAST(ROUND(price*100) AS INTEGER)),
                          purchase_date FROM expenses
                   WHERE used_in=? AND in_inventory=1 ORDER BY id""",
                (pc_name,),
            ).fetchall()
        if not parts:
            raise ValidationError(f"PC '{pc_name}' has no available components.")
        for part in parts:
            if sale_day < _iso_date(part[4]):
                raise ValidationError(f"Sale date cannot be before purchase date {part[4]}.")
        cost_cents = sum(part[3] for part in parts)
        profit_cents = selling_cents - cost_cents
        cursor = conn.execute(
            """INSERT INTO income
               (old_id,name,cost,selling_price,profit,cost_cents,selling_price_cents,
                profit_cents,sale_date,is_pc)
               VALUES (NULL,?,?,?,?,?,?,?,?,1)""",
            (pc_name, cost_cents / 100, selling_price, profit_cents / 100,
             cost_cents, selling_cents, profit_cents, sale_day),
        )
        sale_id = cursor.lastrowid
        first_by_type = {kind: None for kind in COMPONENT_ORDER}
        for position, part in enumerate(parts):
            part_id, _, kind, _, _ = part
            if first_by_type[kind] is None:
                first_by_type[kind] = part_id
            conn.execute(
                "INSERT INTO sold_pc_parts (sale_id,expense_id,component_type,position) VALUES (?,?,?,?)",
                (sale_id, part_id, kind, position),
            )
        conn.execute(
            """INSERT INTO sold_pcs
               (id,name,cpu,cooler,gpu,motherboard,ram,ssd,hdd,pc_case,psu,fan,extra)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (sale_id, pc_name, *[first_by_type[k] for k in COMPONENT_ORDER]),
        )
        conn.executemany(
            "UPDATE expenses SET in_inventory=0 WHERE id=?",
            ((part[0],) for part in parts),
        )
        conn.execute("DELETE FROM assembled_pcs WHERE name=?", (pc_name,))
        return sale_id


def get_sales():
    with connection() as conn:
        rows = conn.execute(
            """SELECT id,old_id,name,
                      COALESCE(cost_cents,CAST(ROUND(cost*100) AS INTEGER)),
                      COALESCE(selling_price_cents,CAST(ROUND(selling_price*100) AS INTEGER)),
                      COALESCE(profit_cents,CAST(ROUND(profit*100) AS INTEGER)),
                      sale_date,is_pc FROM income ORDER BY id"""
        ).fetchall()
    return [
        {"id": r[0], "old_id": r[1], "name": r[2], "cost": r[3] / 100,
         "selling_price": r[4] / 100, "profit": r[5] / 100,
         "sale_date": r[6], "is_pc": bool(r[7])}
        for r in rows
    ]


def undo_sale(sale_id, item_name=None):
    """Undo one sale atomically, restoring the original expense ID(s)."""
    sale_id = _positive_id(sale_id, "Sale ID")
    with connection() as conn:
        sale = conn.execute(
            """SELECT old_id,name,COALESCE(cost_cents,CAST(ROUND(cost*100) AS INTEGER)),is_pc
               FROM income WHERE id=?""",
            (sale_id,),
        ).fetchone()
        if sale is None:
            raise NotFoundError(f"Sale {sale_id} does not exist.")
        old_id, name, cost_cents, is_pc = sale
        if is_pc:
            if conn.execute("SELECT 1 FROM assembled_pcs WHERE name=?", (name,)).fetchone():
                raise ValidationError(
                    f"Cannot undo this sale while an assembled PC named '{name}' exists. "
                    "Rename or remove the current PC first."
                )
            parts = conn.execute(
                """SELECT e.id,e.name,e.type FROM sold_pc_parts spp
                   JOIN expenses e ON e.id=spp.expense_id
                   WHERE spp.sale_id=? ORDER BY spp.position""",
                (sale_id,),
            ).fetchall()
            if not parts:
                legacy = conn.execute("SELECT * FROM sold_pcs WHERE id=?", (sale_id,)).fetchone()
                legacy_ids = [value for value in legacy[2:] if value] if legacy else []
                parts = [conn.execute("SELECT id,name,type FROM expenses WHERE id=?", (part_id,)).fetchone()
                         for part_id in legacy_ids]
                parts = [part for part in parts if part]
            if not parts:
                raise ValidationError(f"Sold PC '{name}' has no recoverable parts.")
            component_names = {kind: [] for kind in COMPONENT_ORDER}
            for part_id, part_name, kind in parts:
                component_names[kind].append(part_name)
                conn.execute(
                    "UPDATE expenses SET in_inventory=1,used_in=? WHERE id=?", (name, part_id)
                )
            cursor = conn.execute(
                """INSERT INTO assembled_pcs
                   (name,price,price_cents,cpu,cooler,gpu,motherboard,ram,ssd,hdd,pc_case,psu,fan,extra)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (name, cost_cents / 100, cost_cents,
                 *[";".join(component_names[k]) for k in COMPONENT_ORDER]),
            )
            pc_id = cursor.lastrowid
            conn.executemany(
                """INSERT INTO assembled_pc_parts (pc_id,expense_id,component_type,position)
                   VALUES (?,?,?,?)""",
                ((pc_id, part_id, kind, position)
                 for position, (part_id, _, kind) in enumerate(parts)),
            )
        else:
            if old_id is None:
                raise ValidationError(f"Sale {sale_id} is missing its original item ID.")
            result = conn.execute("UPDATE expenses SET in_inventory=1 WHERE id=?", (old_id,))
            if result.rowcount != 1:
                raise NotFoundError(f"Original item {old_id} no longer exists.")
        conn.execute("DELETE FROM income WHERE id=?", (sale_id,))


def add_sold_pc(sale_id, name, component_ids):
    """Legacy API retained for callers; new code should use sell_assembled_pc()."""
    values = list(component_ids)
    if len(values) != len(COMPONENT_ORDER):
        raise ValidationError("A sold PC requires 11 component slots.")
    with connection() as conn:
        conn.execute(
            """INSERT INTO sold_pcs
               (id,name,cpu,cooler,gpu,motherboard,ram,ssd,hdd,pc_case,psu,fan,extra)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (_positive_id(sale_id, "Sale ID"), _text(name, "PC name"), *values),
        )


def get_sold_pcs():
    with connection() as conn:
        return conn.execute("SELECT * FROM sold_pcs ORDER BY id").fetchall()


def get_sold_pc_parts(sale_id):
    sale_id = _positive_id(sale_id, "Sale ID")
    with connection() as conn:
        rows = conn.execute(
            """SELECT e.name,e.type,
                      COALESCE(e.price_cents,CAST(ROUND(e.price*100) AS INTEGER)),
                      e.purchase_date FROM sold_pc_parts spp
               JOIN expenses e ON e.id=spp.expense_id
               WHERE spp.sale_id=? ORDER BY spp.position""",
            (sale_id,),
        ).fetchall()
        if not rows:
            rows = conn.execute(
                """SELECT e.name,e.type,
                          COALESCE(e.price_cents,CAST(ROUND(e.price*100) AS INTEGER)),
                          e.purchase_date FROM sold_pcs sp
                   JOIN expenses e ON e.id IN
                     (sp.cpu,sp.cooler,sp.gpu,sp.motherboard,sp.ram,sp.ssd,sp.hdd,sp.pc_case,sp.psu,sp.fan,sp.extra)
                   WHERE sp.id=?""",
                (sale_id,),
            ).fetchall()
    return [{"name": r[0], "type": r[1], "price": r[2] / 100, "purchase_date": r[3]} for r in rows]


def _rename_part(conn, item_id, new_name):
    row = conn.execute("SELECT name,type,used_in FROM expenses WHERE id=?", (item_id,)).fetchone()
    if row is None:
        raise NotFoundError(f"Item {item_id} does not exist.")
    actual_old_name, item_type, used_in = row
    conn.execute("UPDATE expenses SET name=? WHERE id=?", (new_name, item_id))
    if used_in:
        column = COMPONENT_COLUMNS[item_type]
        pc_row = conn.execute(f'SELECT "{column}" FROM assembled_pcs WHERE name=?', (used_in,)).fetchone()
        if pc_row:
            names = (pc_row[0] or "").split(";")
            try:
                names[names.index(actual_old_name)] = new_name
            except ValueError:
                return
            conn.execute(f'UPDATE assembled_pcs SET "{column}"=? WHERE name=?', (";".join(names), used_in))


def rename_part(item_id, old_name, new_name):
    del old_name  # The database value is authoritative.
    with connection() as conn:
        _rename_part(conn, _positive_id(item_id, "Item ID"), _item_name(new_name, "New item name"))


def rename_parts(item_ids, new_name):
    """Rename a displayed group and its PC references atomically."""
    ids = [_positive_id(item_id, "Item ID") for item_id in item_ids]
    new_name = _item_name(new_name, "New item name")
    if not ids:
        raise ValidationError("At least one item must be selected.")
    with connection() as conn:
        for item_id in ids:
            _rename_part(conn, item_id, new_name)


def rename_pc(old_name, new_name):
    old_name = _text(old_name, "Old PC name")
    new_name = _text(new_name, "New PC name")
    with connection() as conn:
        if conn.execute("SELECT 1 FROM assembled_pcs WHERE name=?", (new_name,)).fetchone():
            raise ValidationError(f"A PC named '{new_name}' already exists.")
        result = conn.execute("UPDATE assembled_pcs SET name=? WHERE name=?", (new_name, old_name))
        if result.rowcount != 1:
            raise NotFoundError(f"PC '{old_name}' does not exist.")
        conn.execute("UPDATE expenses SET used_in=? WHERE used_in=?", (new_name, old_name))
