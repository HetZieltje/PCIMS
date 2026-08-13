import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

from db.connection import configure_database, connection
from db.backup import create_backup, restore_backup
from db.queries import (
    NotFoundError,
    ValidationError,
    add_expense,
    assemble_inventory_pc,
    delete_expense,
    get_assembled_pcs,
    get_expenses,
    get_inventory_items,
    get_sales,
    get_sold_pc_parts,
    get_total_pc_price,
    initialize_database,
    rename_parts,
    sell_assembled_pc,
    sell_inventory_items,
    undo_sale,
)


class DatabaseWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "pcims-test.db"
        configure_database(self.database_path)
        initialize_database()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_date_objects_and_default_dates_are_stored_as_iso_dates(self):
        explicit = date(2026, 8, 12)
        first_id = add_expense("CPU", "CPU", 10, explicit)
        second_id = add_expense("RAM", "RAM", 20)

        expenses = {item["id"]: item for item in get_expenses()}
        self.assertEqual(expenses[first_id]["purchase_date"], explicit.isoformat())
        self.assertEqual(expenses[second_id]["purchase_date"], date.today().isoformat())
        self.assertNotEqual(expenses[second_id]["purchase_date"], "CURRENT_DATE")

    def test_integer_cents_are_authoritative_and_round_half_up(self):
        item_id = add_expense("CPU", "CPU", "1.005")
        with connection() as database:
            stored = database.execute(
                "SELECT price,price_cents FROM expenses WHERE id=?", (item_id,)
            ).fetchone()
            database.execute("UPDATE expenses SET price=999.99 WHERE id=?", (item_id,))

        self.assertEqual(stored, (1.01, 101))
        self.assertEqual(get_expenses()[0]["price"], 1.01)

    def test_standalone_sale_and_undo_restore_the_original_expense(self):
        add_expense("Unrelated", "Extra", 1)
        item_id = add_expense("CPU", "CPU", 100, date.today())

        sale_id = sell_inventory_items([item_id], 125, date.today())[0]
        self.assertNotEqual(sale_id, item_id)
        self.assertNotIn(item_id, [row[0] for row in get_inventory_items()])

        undo_sale(sale_id)
        self.assertIn(item_id, [row[0] for row in get_inventory_items()])
        self.assertEqual(get_sales(), [])

    def test_sold_expense_cannot_be_deleted_until_sale_is_undone(self):
        item_id = add_expense("CPU", "CPU", 100)
        sale_id = sell_inventory_items([item_id], 125, date.today())[0]

        with self.assertRaises(ValidationError):
            delete_expense(item_id)

        undo_sale(sale_id)
        self.assertTrue(delete_expense(item_id))

    def test_standalone_sale_rolls_back_when_any_item_is_invalid(self):
        item_id = add_expense("CPU", "CPU", 100, date.today())

        with self.assertRaises(NotFoundError):
            sell_inventory_items([item_id, 9999], 200, date.today())

        self.assertIn(item_id, [row[0] for row in get_inventory_items()])
        self.assertEqual(get_sales(), [])

    def test_group_sale_allocates_every_cent(self):
        ids = [add_expense(f"RAM {index}", "RAM", 10) for index in range(3)]

        sell_inventory_items(ids, 100, date.today())

        prices = [sale["selling_price"] for sale in get_sales()]
        self.assertEqual(prices, [33.34, 33.33, 33.33])
        self.assertEqual(round(sum(prices), 2), 100.00)

    def test_backup_is_valid_and_retention_is_enforced(self):
        add_expense("CPU", "CPU", 100)
        backup_directory = Path(self.temporary_directory.name) / "backups"

        for _ in range(3):
            create_backup(backup_directory, keep=2)

        backups = list(backup_directory.glob("pcims_db_*.db"))
        self.assertEqual(len(backups), 2)
        for backup in backups:
            with closing(sqlite3.connect(f"file:{backup.as_posix()}?mode=ro", uri=True)) as database:
                self.assertEqual(database.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_restore_is_atomic_and_preserves_a_pre_restore_backup(self):
        add_expense("Before backup", "CPU", 10)
        source_directory = Path(self.temporary_directory.name) / "source-backups"
        source_backup = create_backup(source_directory)
        add_expense("After backup", "RAM", 20)
        safety_directory = Path(self.temporary_directory.name) / "safety-backups"

        safety_backup = restore_backup(source_backup, safety_directory)

        self.assertEqual([item["name"] for item in get_expenses()], ["Before backup"])
        self.assertEqual(safety_backup.parent, safety_directory)
        with closing(sqlite3.connect(f"file:{safety_backup.as_posix()}?mode=ro", uri=True)) as database:
            self.assertEqual(database.execute("SELECT COUNT(*) FROM expenses").fetchone()[0], 2)

    def test_invalid_restore_does_not_change_the_live_database(self):
        add_expense("Keep me", "CPU", 10)
        invalid_backup = Path(self.temporary_directory.name) / "invalid.db"
        invalid_backup.write_text("not a sqlite database", encoding="utf-8")

        with self.assertRaises(sqlite3.DatabaseError):
            restore_backup(invalid_backup)

        self.assertEqual([item["name"] for item in get_expenses()], ["Keep me"])

    def test_restore_stages_source_before_retention_can_prune_it(self):
        add_expense("Old state", "CPU", 10)
        backup_directory = Path(self.temporary_directory.name) / "backups"
        oldest_backup = create_backup(backup_directory)
        add_expense("New state", "RAM", 20)
        for _ in range(13):
            create_backup(backup_directory)

        restore_backup(oldest_backup)

        self.assertEqual([item["name"] for item in get_expenses()], ["Old state"])

    def test_sale_before_purchase_date_is_rejected_without_changes(self):
        tomorrow = date.today() + timedelta(days=1)
        item_id = add_expense("CPU", "CPU", 100, tomorrow)

        with self.assertRaises(ValidationError):
            sell_inventory_items([item_id], 100, date.today())

        self.assertIn(item_id, [row[0] for row in get_inventory_items()])

    def test_assembly_rolls_back_if_a_selected_component_disappears(self):
        item_id = add_expense("CPU", "CPU", 100)

        with self.assertRaises(NotFoundError):
            assemble_inventory_pc("PC 1", {"CPU": "CPU", "RAM": "Missing"})

        self.assertEqual(get_assembled_pcs(), [])
        with connection() as database:
            used_in = database.execute("SELECT used_in FROM expenses WHERE id=?", (item_id,)).fetchone()[0]
        self.assertIsNone(used_in)

    def test_pc_sale_and_undo_preserve_multiple_parts_of_the_same_type(self):
        part_ids = [
            add_expense("CPU", "CPU", 100),
            add_expense("RAM", "RAM", 40),
            add_expense("RAM", "RAM", 40),
        ]
        assemble_inventory_pc("PC 1", {"CPU": "CPU", "RAM": "RAM;RAM"})

        sale_id = sell_assembled_pc("PC 1", 250, date.today())
        self.assertEqual(len(get_sold_pc_parts(sale_id)), 3)
        self.assertEqual(get_assembled_pcs(), [])

        undo_sale(sale_id)
        self.assertEqual(len(get_assembled_pcs()), 1)
        inventory_ids = [row[0] for row in get_inventory_items()]
        self.assertEqual(set(inventory_ids), set(part_ids))
        with connection() as database:
            assignments = database.execute(
                "SELECT used_in FROM expenses ORDER BY id"
            ).fetchall()
        self.assertEqual(assignments, [("PC 1",), ("PC 1",), ("PC 1",)])

    def test_pc_undo_name_collision_is_rejected_without_partial_changes(self):
        add_expense("Old CPU", "CPU", 100)
        assemble_inventory_pc("PC 1", {"CPU": "Old CPU"})
        sale_id = sell_assembled_pc("PC 1", 125, date.today())
        add_expense("New CPU", "CPU", 80)
        assemble_inventory_pc("PC 1", {"CPU": "New CPU"})

        with self.assertRaisesRegex(ValidationError, "assembled PC named 'PC 1'"):
            undo_sale(sale_id)

        self.assertEqual(len(get_sales()), 1)
        self.assertEqual(len(get_assembled_pcs()), 1)

    def test_group_rename_updates_each_component_reference(self):
        ram_ids = [add_expense("RAM", "RAM", 40), add_expense("RAM", "RAM", 40)]
        assemble_inventory_pc("PC 1", {"RAM": "RAM;RAM"})

        rename_parts(ram_ids, "Renamed RAM")

        pc = get_assembled_pcs()[0]
        self.assertEqual(pc[7], "Renamed RAM;Renamed RAM")
        self.assertEqual([item["name"] for item in get_expenses()], ["Renamed RAM", "Renamed RAM"])

    def test_normalized_membership_remains_authoritative(self):
        part_ids = [add_expense("CPU", "CPU", 100), add_expense("RAM", "RAM", 50)]
        assemble_inventory_pc("PC 1", {"CPU": "CPU", "RAM": "RAM"})
        with connection() as database:
            database.execute("UPDATE expenses SET used_in='stale legacy value'")

        self.assertEqual(get_total_pc_price("PC 1"), 150)
        sale_id = sell_assembled_pc("PC 1", 200, date.today())
        self.assertEqual(get_inventory_items(), [])
        undo_sale(sale_id)
        self.assertEqual({row[0] for row in get_inventory_items()}, set(part_ids))

    def test_item_names_cannot_use_the_legacy_component_delimiter(self):
        with self.assertRaises(ValidationError):
            add_expense("RAM;GPU", "RAM", 10)


class SchemaMigrationTests(unittest.TestCase):
    def test_legacy_schema_is_upgraded_and_backfilled(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "legacy.db"
            with closing(sqlite3.connect(database_path)) as database:
                database.executescript(
                    """
                    CREATE TABLE expenses (
                        id INTEGER PRIMARY KEY, name TEXT, type TEXT, price REAL,
                        purchase_date TEXT, in_inventory INTEGER, used_in TEXT
                    );
                    CREATE TABLE assembled_pcs (
                        id INTEGER PRIMARY KEY, name TEXT, price REAL,
                        cpu TEXT, cooler TEXT, gpu TEXT, motherboard TEXT, ram TEXT,
                        ssd TEXT, hdd TEXT, pc_case TEXT, psu TEXT, fan TEXT, extra TEXT
                    );
                    CREATE TABLE income (
                        id INTEGER PRIMARY KEY, old_id INTEGER, name TEXT, cost REAL,
                        selling_price REAL, profit REAL, sale_date TEXT, is_pc INTEGER
                    );
                    INSERT INTO expenses VALUES
                        (1,'CPU','CPU',99.99,'2026-01-01',1,'Legacy PC');
                    INSERT INTO assembled_pcs
                        (id,name,price,cpu) VALUES (1,'Legacy PC',99.99,'CPU');
                    INSERT INTO income VALUES
                        (1,NULL,'Prior sale',5.55,10.10,4.55,'2026-01-02',0);
                    """
                )
                database.commit()

            configure_database(database_path)
            initialize_database()

            with connection() as database:
                self.assertEqual(database.execute("PRAGMA user_version").fetchone()[0], 2)
                self.assertEqual(database.execute(
                    "SELECT price_cents FROM expenses WHERE id=1"
                ).fetchone()[0], 9999)
                self.assertEqual(database.execute(
                    "SELECT cost_cents,selling_price_cents,profit_cents FROM income WHERE id=1"
                ).fetchone(), (555, 1010, 455))
                self.assertEqual(database.execute(
                    "SELECT pc_id,expense_id,component_type,position FROM assembled_pc_parts"
                ).fetchone(), (1, 1, "CPU", 0))


if __name__ == "__main__":
    unittest.main()
