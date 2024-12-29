import sqlite3

def column_exists(cursor, table, column):
    cursor.execute(f"PRAGMA table_info({table});")
    columns = [col[1] for col in cursor.fetchall()]
    return column in columns

def table_exists(cursor, table):
    cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}';")
    return cursor.fetchone() is not None

def convert_db_schema(db_path):
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Backup existing tables
        tables_to_rename = ["inventory", "expenses", "income", "assembled_pcs"]
        for table in tables_to_rename:
            if table_exists(cursor, table):
                cursor.execute(f"ALTER TABLE {table} RENAME TO old_{table};")

        # Create new schema-compliant tables
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                type TEXT NOT NULL CHECK (type IN ('CPU', 'Cooler', 'GPU', 'Motherboard', 'RAM', 'SSD', 'HDD', 'Case', 'PSU', 'Fan', 'Extra')),
                price REAL NOT NULL,
                purchase_date DATE DEFAULT '2024-01-01' NOT NULL,
                in_inventory BOOLEAN NOT NULL DEFAULT 1,
                used_in TEXT
            )
        ''')

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

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS income (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                cost REAL NOT NULL,
                selling_price REAL NOT NULL,
                profit REAL NOT NULL,
                sale_date DATE DEFAULT '2024-01-01' NOT NULL
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sold_pcs (
                id INTEGER PRIMARY KEY,
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

        # Migrate data from old tables to new tables
        if table_exists(cursor, "old_expenses"):
            cursor.execute('''
                INSERT INTO expenses (name, type, price, purchase_date)
                SELECT name, type, price, '2024-01-01' FROM old_expenses;
            ''')

        if table_exists(cursor, "old_inventory"):
            cursor.execute('''
                UPDATE expenses
                SET in_inventory = 1, used_in = (
                    SELECT used_in FROM old_inventory WHERE old_inventory.name = expenses.name AND old_inventory.type = expenses.type AND old_inventory.price = expenses.price
                )
                WHERE (name, type, price) IN (SELECT name, type, price FROM old_inventory)
            ''')

        if table_exists(cursor, "old_income"):
            cursor.execute('''
                INSERT INTO income (name, cost, selling_price, profit, sale_date)
                SELECT name, cost, selling_price, profit, '2024-01-01' FROM old_income;
            ''')

        if table_exists(cursor, "old_assembled_pcs"):
            cursor.execute('''
                INSERT INTO assembled_pcs (name, price, cpu, cooler, gpu, motherboard, ram, ssd, hdd, pc_case, psu, fan, extra)
                SELECT name, price, cpu, cooler, gpu, motherboard, ram, ssd, hdd, pc_case, psu, fan, extra FROM old_assembled_pcs;
            ''')

        # Set in_inventory to 0 for items in expenses but not in inventory
        cursor.execute('''
            UPDATE expenses
            SET in_inventory = 0
            WHERE (name, type, price) NOT IN (SELECT name, type, price FROM old_inventory)
        ''')

        # Drop old tables
        for table in tables_to_rename:
            if table_exists(cursor, f"old_{table}"):
                cursor.execute(f"DROP TABLE IF EXISTS old_{table};")

        conn.commit()
        print("Database schema converted successfully.")

    except sqlite3.Error as e:
        print(f"Error during conversion: {e}")

    finally:
        conn.close()

if __name__ == "__main__":
    db_path = "db/pcims_db.db"
    convert_db_schema(db_path)
