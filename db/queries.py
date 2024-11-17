from db.connection import get_connection

# Initialize the database with required tables
def initialize_database():
    conn = get_connection()
    cursor = conn.cursor()

    # Enable foreign key support
    cursor.execute("PRAGMA foreign_keys = ON")

    # Expenses table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            type TEXT NOT NULL,
            price REAL NOT NULL
        )
    ''')

    # Inventory table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            type TEXT NOT NULL CHECK (type IN ('CPU', 'Cooler', 'Motherboard', 'RAM', 'SSD', 'HDD', 'GPU', 'Case', 'PSU', 'Fan', 'Extra')),
            price REAL NOT NULL,
            used_in INTEGER REFERENCES assembled_pcs (id) ON DELETE CASCADE,
            FOREIGN KEY (id) REFERENCES expenses (id) ON DELETE CASCADE
        )
    ''')

    # Assembled PCs table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS assembled_pcs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            cpu1 TEXT,
            cpu2 TEXT,
            cooler1 TEXT,
            cooler2 TEXT,
            motherboard TEXT,
            ram TEXT,
            ssd1 TEXT,
            ssd2 TEXT,
            hdd1 TEXT,
            hdd2 TEXT,
            gpu1 TEXT,
            gpu2 TEXT,
            pc_case TEXT,
            psu TEXT,
            fan1 TEXT,
            fan2 TEXT,
            fan3 TEXT,
            extra1 TEXT,
            extra2 TEXT,
            extra3 TEXT      
        )
    ''')

    # Income table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS income (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL CHECK (type IN ('PC', 'Inventory')),
            cost REAL NOT NULL,
            selling_price REAL NOT NULL,
            profit REAL NOT NULL
        )
    ''')

    # Sold PCs table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sold_pcs (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            cpu1 INTEGER REFERENCES expenses(id),
            cpu2 INTEGER REFERENCES expenses(id),
            cooler1 INTEGER REFERENCES expenses(id),
            cooler2 INTEGER REFERENCES expenses(id),
            gpu1 INTEGER REFERENCES expenses(id),
            gpu2 INTEGER REFERENCES expenses(id),
            motherboard INTEGER REFERENCES expenses(id),
            ram INTEGER REFERENCES expenses(id),
            ssd1 INTEGER REFERENCES expenses(id),
            ssd2 INTEGER REFERENCES expenses(id),
            hdd1 INTEGER REFERENCES expenses(id),
            hdd2 INTEGER REFERENCES expenses(id),
            pc_case INTEGER REFERENCES expenses(id),
            psu INTEGER REFERENCES expenses(id),
            fan1 INTEGER REFERENCES expenses(id),
            fan2 INTEGER REFERENCES expenses(id),
            fan3 INTEGER REFERENCES expenses(id),
            extra1 INTEGER REFERENCES expenses(id),
            extra2 INTEGER REFERENCES expenses(id),
            extra3 INTEGER REFERENCES expenses(id),
            sale_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    conn.commit()
    conn.close()


# Inventory Queries
def add_item_to_inventory(name, item_type, price, used_in=None):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO inventory (name, type, price, used_in)
        VALUES (?, ?, ?, ?)
    """, (name, item_type, price, used_in))
    conn.commit()
    conn.close()


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
def add_expense(item_id, name, item_type, price):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO expenses (id, name, type, price)
        VALUES (?, ?, ?, ?)
    """, (item_id, name, item_type, price))
    conn.commit()
    conn.close()


def delete_expense(item_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM expenses WHERE id=?", (item_id))
    conn.commit()
    conn.close()


def get_expenses():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM expenses")
    items = cursor.fetchall()
    conn.close()
    return [{"name": row[1], "type": row[2], "price": row[3]} for row in items]


# Assembled PCs Queries
def assemble_pc(pc_name, price, components):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO assembled_pcs (
            name, price, cpu1, cpu2, cooler1, cooler2, gpu1, gpu2, motherboard, ram, ssd1, ssd2, hdd1,
            hdd2, pc_case, psu, fan1, fan2, fan3, extra1, extra2, extra3
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
def add_income(id, name, cost, selling_price, profit):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO income (name, cost, selling_price, profit)
        VALUES (?, ?, ?, ?)
    """, (id, name, cost, selling_price, profit))
    conn.commit()
    conn.close()


def get_sold_items():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM income")
    items = cursor.fetchall()
    conn.close()
    return [{"name": row[1], "cost": row[2], "selling_price": row[3], "profit": row[4]} for row in items]


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
