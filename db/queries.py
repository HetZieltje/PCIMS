from db.connection import get_connection
import uuid

# Initialize the database with required tables
def initialize_database():
    conn = get_connection()
    cursor = conn.cursor()

    # Enable foreign key support
    cursor.execute("PRAGMA foreign_keys = ON")

    # Expenses table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS expenses (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            type TEXT NOT NULL CHECK (type IN ('CPU', 'Cooler', 'GPU','Motherboard', 'RAM', 'SSD', 'HDD', 'Case', 'PSU', 'Fan', 'Extra')),
            price REAL NOT NULL,
            purchase_date DATE DEFAULT CURRENT_DATE NOT NULL
        )
    ''')

    # Inventory table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS inventory (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            type TEXT NOT NULL CHECK (type IN ('CPU', 'Cooler', 'GPU','Motherboard', 'RAM', 'SSD', 'HDD', 'Case', 'PSU', 'Fan', 'Extra')),
            price REAL NOT NULL,
            used_in TEXT,
            FOREIGN KEY (id) REFERENCES expenses (id) ON DELETE CASCADE
        )
    ''')

    # Assembled PCs table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS assembled_pcs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            price REAL NOT NULL,
            cpu TEXT,
            cooler TEXT,
            gpu TEXT,
            motherboard TEXT,
            ram TEXT,
            ssd TEXT,
            hdd TEXT,
            pc_case TEXT,
            psu TEXT,
            fan TEXT,
            extra TEXT
        )
    ''')

    # Income table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS income (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            cost REAL NOT NULL,
            selling_price REAL NOT NULL,
            profit REAL NOT NULL,
            sale_date DATE DEFAULT CURRENT_DATE NOT NULL
        )
    ''')

    # Sold PCs table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sold_pcs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            cpu INTEGER REFERENCES expenses(id),
            cooler INTEGER REFERENCES expenses(id),
            gpu INTEGER REFERENCES expenses(id),
            motherboard INTEGER REFERENCES expenses(id),
            ram INTEGER REFERENCES expenses(id),
            ssd INTEGER REFERENCES expenses(id),
            hdd INTEGER REFERENCES expenses(id),
            pc_case INTEGER REFERENCES expenses(id),
            psu INTEGER REFERENCES expenses(id),
            fan1 INTEGER REFERENCES expenses(id),
            extra INTEGER REFERENCES expenses(id)
        )
    ''')

    conn.commit()
    conn.close()

# Inventory Queries
def add_item_to_inventory(name, item_type, price, used_in=None):
    item_id = str(uuid.uuid4())
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO inventory (id, name, type, price, used_in)
        VALUES (?, ?, ?, ?, ?)
    """, (item_id, name, item_type, price, used_in))
    conn.commit()
    conn.close()
    return item_id


def get_inventory_items():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, type, price, used_in FROM inventory")
    items = cursor.fetchall()
    conn.close()
    return items


def delete_item_from_inventory(item_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM inventory WHERE id=?", (item_id,))
    conn.commit()
    conn.close()

def delete_components_used_in_pc(pc_name):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM inventory WHERE used_in=?", (pc_name,))
    conn.commit()
    conn.close()

def update_used_in_component(pc_name, name, item_type):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE inventory 
        SET used_in = ? 
        WHERE name = ? AND type = ? AND used_in IS NULL 
        AND id = (
            SELECT id 
            FROM inventory 
            WHERE name = ? AND type = ? AND used_in IS NULL 
            ORDER BY id 
            LIMIT 1
        )
    """, (pc_name, name, item_type, name, item_type))
    conn.commit()
    conn.close()


def update_used_in_for_deleted_pc(deleted_pc_name):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE inventory SET used_in = NULL WHERE used_in = ?", (deleted_pc_name,))
    conn.commit()
    conn.close()


def get_total_pc_price(pc_name):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT SUM(price) FROM inventory WHERE used_in=?", (pc_name,))
    total_price = cursor.fetchone()[0]
    conn.close()
    return total_price if total_price else 0


# Expense Queries
def add_expense(item_id, name, item_type, price, purchase_date="CURRENT_DATE"):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO expenses (id, name, type, price, purchase_date)
        VALUES (?, ?, ?, ?, ?)
    """, (item_id, name, item_type, price, purchase_date))
    conn.commit()
    conn.close()


def delete_expense(item_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM expenses WHERE id=?", (item_id,))
    conn.commit()
    conn.close()


def get_expenses():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM expenses")
    items = cursor.fetchall()
    conn.close()
    return [{"id": row[0], "name": row[1], "type": row[2], "price": row[3], "purchase_date": row[4]} for row in items]


def get_inventory_value():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT SUM(price) FROM inventory")
    inventory_value = cursor.fetchone()[0]
    conn.close()
    return inventory_value if inventory_value else 0

def get_purchase_date(item_id):
    expenses = get_expenses()
    for expense in expenses:
        if expense['id'] == item_id:
            return expense['purchase_date']
    return None


# Assembled PCs Queries
def assemble_pc(pc_name, price, components):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO assembled_pcs (
            name, price, cpu, cooler, gpu, motherboard, ram, ssd, hdd, pc_case, psu, fan, extra
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (pc_name, price, *components.values()))

    conn.commit()
    conn.close()


def get_assembled_pcs():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM assembled_pcs")
    pcs = cursor.fetchall()
    conn.close()
    return pcs


def delete_assembled_pc(pc_name):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM assembled_pcs WHERE name=?", (pc_name,))
    conn.commit()
    conn.close()


def get_pc_names():
    """Fetch all assembled PC names from the database."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM assembled_pcs")
    pc_names = [row[0] for row in cursor.fetchall()]
    conn.close()
    return pc_names


# Income Queries
def add_income(name, cost, selling_price, profit, sale_date):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO income (name, cost, selling_price, profit, sale_date)
        VALUES (?, ?, ?, ?, ?)
    """, (name, cost, selling_price, profit, sale_date))
    conn.commit()
    conn.close()


def get_sales():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM income")
    items = cursor.fetchall()
    conn.close()
    return [{"name": row[1], "cost": row[2], "selling_price": row[3], "profit": row[4], "sale_date": row[5]} for row in items]


def undo_sale(sold_pc_id):
    conn = get_connection()
    cursor = conn.cursor()

    # Retrieve part IDs from `sold_pcs`
    cursor.execute("SELECT * FROM sold_pcs WHERE id=?", (sold_pc_id,))
    sold_pc = cursor.fetchone()

    if sold_pc:
        part_id_indexes = range(3, len(sold_pc) - 1)  # Indexes of part IDs in the record

        # Re-add each part to `inventory` using information from `expenses`
        for i in part_id_indexes:
            part_id = sold_pc[i]
            if part_id:
                cursor.execute("SELECT name, type, price FROM expenses WHERE id=?", (part_id,))
                part_info = cursor.fetchone()
                if part_info:
                    cursor.execute("""
                        INSERT INTO inventory (name, type, price, used_in)
                        VALUES (?, ?, ?, NULL)
                    """, (part_info[0], part_info[1], part_info[2]))

        # Remove the sold PC record from `sold_pcs`
        cursor.execute("DELETE FROM sold_pcs WHERE id=?", (sold_pc_id,))

    conn.commit()
    conn.close()
