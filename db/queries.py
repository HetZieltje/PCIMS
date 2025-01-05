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
            type TEXT NOT NULL CHECK (type IN ('CPU', 'Cooler', 'GPU','Motherboard', 'RAM', 'SSD', 'HDD', 'Case', 'PSU', 'Fan', 'Extra')),
            price REAL NOT NULL,
            purchase_date DATE DEFAULT CURRENT_DATE NOT NULL,
            in_inventory BOOLEAN NOT NULL DEFAULT 1,
            used_in TEXT
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
            old_id INTEGER,
            name TEXT NOT NULL,
            cost REAL NOT NULL,
            selling_price REAL NOT NULL,
            profit REAL NOT NULL,
            sale_date DATE DEFAULT CURRENT_DATE NOT NULL,
            is_pc BOOLEAN NOT NULL DEFAULT 0
        )
    ''')

    # Sold PCs table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sold_pcs (
            id INTEGER PRIMARY KEY,
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
            extra INTEGER REFERENCES expenses(id),
            FOREIGN KEY(id) REFERENCES income(id) ON DELETE CASCADE
        )
    ''')

    conn.commit()
    conn.close()

# Inventory Queries
def add_item_to_inventory(id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE expenses SET in_inventory = 1 WHERE id = ?", (id,))
    conn.commit()
    conn.close()

def get_inventory_items():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, type, price, used_in FROM expenses WHERE in_inventory = 1")
    items = cursor.fetchall()
    conn.close()
    return items

def delete_item_from_inventory(id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE expenses SET in_inventory = 0 WHERE id = ?", (id,))
    conn.commit()
    conn.close()

def delete_components_used_in_pc(pc_name):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE expenses SET in_inventory = 0 WHERE used_in = ?", (pc_name,))
    conn.commit()
    conn.close()

def update_used_in_component(pc_name, names, item_type):
    conn = get_connection()
    cursor = conn.cursor()
    for name in names.split(';'):
        cursor.execute("""
            UPDATE expenses 
            SET used_in = ? 
            WHERE name = ? AND type = ? AND used_in IS NULL AND in_inventory = 1
            AND id = (
                SELECT id 
                FROM expenses 
                WHERE name = ? AND type = ? AND used_in IS NULL AND in_inventory = 1
                ORDER BY id 
                LIMIT 1
            )
        """, (pc_name, name, item_type, name, item_type))
    conn.commit()
    conn.close()

def update_used_in_for_deleted_pc(deleted_pc_name):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE expenses SET used_in = NULL WHERE used_in = ?", (deleted_pc_name,))
    conn.commit()
    conn.close()

def get_total_pc_price(pc_name):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT SUM(price) FROM expenses WHERE used_in=?", (pc_name,))
    total_price = cursor.fetchone()[0]
    conn.close()
    return total_price if total_price else 0

# Expense Queries
def add_expense(name, item_type, price, purchase_date="CURRENT_DATE"):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO expenses (name, type, price, purchase_date)
        VALUES (?, ?, ?, ?)
    """, (name, item_type, price, purchase_date))
    id = cursor.lastrowid
    conn.commit()
    conn.close()

    return id

def delete_expense(id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM expenses WHERE id=?", (id,))
    conn.commit()
    conn.close()

def get_expenses():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM expenses")
    items = cursor.fetchall()
    conn.close()
    return [{"id": row[0], "name": row[1], "type": row[2], "price": row[3], "purchase_date": row[4], "in_inventory": row[5], "used_in": row[6]} for row in items]

def get_inventory_value():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT SUM(price) FROM expenses WHERE in_inventory = 1")
    inventory_value = cursor.fetchone()[0]
    conn.close()
    return inventory_value if inventory_value else 0

def get_purchase_date(id):
    expenses = get_expenses()
    for expense in expenses:
        if expense['id'] == int(id):
            return expense['purchase_date']
    return None

def get_item_cost(item_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT price FROM expenses WHERE id = ?", (item_id,))
    cost = cursor.fetchone()[0]
    conn.close()
    return cost

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
def add_income(old_id, name, cost, selling_price, profit, sale_date, is_pc=False):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO income (old_id, name, cost, selling_price, profit, sale_date, is_pc)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (old_id, name, cost, selling_price, profit, sale_date, is_pc))
    conn.commit()
    id = cursor.lastrowid
    conn.close()
    return id

def get_sales():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM income")
    items = cursor.fetchall()
    conn.close()
    return [{"id": row[0], "old_id": row[1], "name": row[2], "cost": row[3], "selling_price": row[4], "profit": row[5], "sale_date": row[6], "is_pc": row[7]} for row in items]

def undo_sale(sold_item_id, item_name):
    conn = get_connection()
    cursor = conn.cursor()

    # Retrieve part IDs and name from `sold_pcs`
    cursor.execute("SELECT * FROM sold_pcs WHERE id=? AND name=?", (sold_item_id, item_name))
    sold_pc = cursor.fetchone()

    if sold_pc:
        part_id_indexes = range(2, len(sold_pc))  # Indexes of part IDs in the record

        # Re-add each part to `expenses` using information from `expenses`
        for i in part_id_indexes:
            part_id = sold_pc[i]
            if part_id:
                cursor.execute("UPDATE expenses SET in_inventory = 1 WHERE id=?", (part_id,))

        # Retrieve the PC name and price from the `income` table
        cursor.execute("SELECT name, cost FROM income WHERE id=?", (sold_item_id,))
        pc_info = cursor.fetchone()
        pc_name, price = pc_info[0], pc_info[1]

        # Retrieve the full names of each part and ensure correct order
        component_names = {component: [] for component in ["cpu", "cooler", "gpu", "motherboard", "ram", "ssd", "hdd", "case", "psu", "fan", "extra"]}
        for i, part_id in enumerate(sold_pc[2:]):
            if part_id:
                cursor.execute("SELECT name, type FROM expenses WHERE id=?", (part_id,))
                component_name, component_type = cursor.fetchone()
                component_names[component_type.lower()].append(component_name)

        # Prepare the components for insertion
        ordered_components = {component: ';'.join(component_names[component]) for component in ["cpu", "cooler", "gpu", "motherboard", "ram", "ssd", "hdd", "case", "psu", "fan", "extra"]}

        # Reinsert the PC into the `assembled_pcs` table
        cursor.execute("""
            INSERT INTO assembled_pcs (name, price, cpu, cooler, gpu, motherboard, ram, ssd, hdd, pc_case, psu, fan, extra)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (pc_name, price, *ordered_components.values()))

        # Remove the sold PC record from `sold_pcs`
        cursor.execute("DELETE FROM sold_pcs WHERE id=?", (sold_item_id,))

    else:
        # If no record is found in `sold_pcs`, it might be an individual item
        cursor.execute("UPDATE expenses SET in_inventory = 1 WHERE id=?", (sold_item_id,))

    # Remove the income record
    cursor.execute("DELETE FROM income WHERE id=?", (sold_item_id,))

    conn.commit()
    conn.close()

def add_sold_pc(id, name, component_ids):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO sold_pcs (id, name, cpu, cooler, gpu, motherboard, ram, ssd, hdd, pc_case, psu, fan, extra)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (id, name, *component_ids))
    conn.commit()
    conn.close()

def get_sold_pcs():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM sold_pcs")
    pcs = cursor.fetchall()
    conn.close()
    return pcs

def get_sold_pc_parts(id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT e.name, e.type, e.price, e.purchase_date
        FROM sold_pcs sp
        JOIN expenses e ON sp.cpu = e.id OR sp.cooler = e.id OR sp.gpu = e.id OR sp.motherboard = e.id OR sp.ram = e.id OR sp.ssd = e.id OR sp.hdd = e.id OR sp.pc_case = e.id OR sp.psu = e.id OR sp.fan = e.id OR sp.extra = e.id
        WHERE sp.id = ?
    """, (id,))
    parts = cursor.fetchall()
    conn.close()
    return [{"name": row[0], "type": row[1], "price": row[2], "purchase_date": row[3]} for row in parts]

def rename_part(item_id, old_name, new_name):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE expenses SET name = ? WHERE id = ?", (new_name, item_id))
    
    # Get the type of the item
    cursor.execute("SELECT type, used_in FROM expenses WHERE id = ?", (item_id,))
    result = cursor.fetchone()
    item_type, used_in = result[0], result[1]

    # Update the name in the assembled PCs
    if used_in:
        if item_type.lower() == "case":
            column_name = "pc_case"
        else:
            column_name = item_type.lower()
        cursor.execute(f"""
            UPDATE assembled_pcs
            SET "{column_name}" = REPLACE("{column_name}", ?, ?)
            WHERE name = ?
        """, (old_name, new_name, used_in))
    
    conn.commit()
    conn.close()

def rename_pc(old_name, new_name):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE assembled_pcs SET name = ? WHERE name = ?", (new_name, old_name))
    cursor.execute("UPDATE expenses SET used_in = ? WHERE used_in = ?", (new_name, old_name))
    conn.commit()
    conn.close()
