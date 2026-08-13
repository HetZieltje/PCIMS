import sqlite3
import tempfile
import unittest
from contextlib import closing, contextmanager
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from pcims.db.backup import create_backup, restore_backup, validate_database
from pcims.db.connection import configure_database, connection
from pcims.db.queries import (
    SCHEMA_VERSION,
    DatabaseIntegrityError,
    NotFoundError,
    SchemaVersionError,
    ValidationError,
    add_expenses,
    assemble_pc,
    delete_expenses,
    disassemble_pc,
    get_financial_summary,
    initialize_database,
    list_expenses,
    list_inventory,
    list_pcs,
    list_sales,
    rename_expenses,
    rename_pc,
    sell_items,
    sell_pc,
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

    def buy(self, name, item_type, price, purchase_date=None):
        return add_expenses(
            [
                {
                    "name": name,
                    "item_type": item_type,
                    "price": price,
                    "purchase_date": purchase_date,
                }
            ]
        )[0]

    def test_schema_contains_only_authoritative_current_tables_and_columns(self):
        with connection() as database:
            self.assertEqual(
                database.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION
            )
            tables = {
                row[0]
                for row in database.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            }
            expense_columns = [
                row[1] for row in database.execute("PRAGMA table_info(expenses)")
            ]
            pc_columns = [
                row[1] for row in database.execute("PRAGMA table_info(assembled_pcs)")
            ]
            sale_columns = [
                row[1] for row in database.execute("PRAGMA table_info(sales)")
            ]

        self.assertEqual(
            tables, {"expenses", "assembled_pcs", "pc_parts", "sales", "sale_items"}
        )
        self.assertEqual(
            expense_columns, ["id", "name", "item_type", "price_cents", "purchase_date"]
        )
        self.assertEqual(pc_columns, ["id", "name"])
        self.assertEqual(
            sale_columns,
            ["id", "name", "kind", "cost_cents", "selling_price_cents", "sale_date"],
        )

    def test_membership_indexes_cover_display_order(self):
        with connection() as database:
            for table, index_name in (
                ("pc_parts", "pc_parts_in_display_order"),
                ("sale_items", "sale_items_in_display_order"),
            ):
                plan = " ".join(
                    row[3]
                    for row in database.execute(
                        f"EXPLAIN QUERY PLAN SELECT * FROM {table} "
                        "WHERE "
                        + ("pc_id" if table == "pc_parts" else "sale_id")
                        + "=1 ORDER BY position"
                    )
                )
                self.assertIn(index_name, plan)
                self.assertNotIn("TEMP B-TREE", plan)

    def test_initialize_restores_current_schema_indexes_idempotently(self):
        with connection() as database:
            database.execute("DROP INDEX pc_parts_in_display_order")
            database.execute("DROP INDEX sale_items_in_display_order")

        initialize_database()

        with connection() as database:
            indexes = {
                row[0]
                for row in database.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                )
            }
        self.assertIn("pc_parts_in_display_order", indexes)
        self.assertIn("sale_items_in_display_order", indexes)

    def test_incompatible_schema_is_rejected_instead_of_mutated(self):
        legacy_path = Path(self.temporary_directory.name) / "legacy.db"
        with closing(sqlite3.connect(legacy_path)) as database:
            database.execute(
                "CREATE TABLE expenses (id INTEGER PRIMARY KEY, price REAL)"
            )
            database.execute("PRAGMA user_version=2")
        configure_database(legacy_path)

        with self.assertRaisesRegex(SchemaVersionError, "incompatible"):
            initialize_database()

        with closing(sqlite3.connect(legacy_path)) as database:
            self.assertEqual(database.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertEqual(
                [row[1] for row in database.execute("PRAGMA table_info(expenses)")],
                ["id", "price"],
            )

    def test_current_version_with_wrong_layout_is_rejected(self):
        with connection() as database:
            database.execute(
                "ALTER TABLE expenses RENAME COLUMN price_cents TO price_value"
            )

        with self.assertRaisesRegex(SchemaVersionError, "incompatible"):
            initialize_database()

    def test_current_schema_rejects_extra_tables_and_missing_triggers(self):
        with connection() as database:
            database.execute("CREATE TABLE legacy_income (id INTEGER PRIMARY KEY)")
        with self.assertRaisesRegex(SchemaVersionError, "incompatible"):
            initialize_database()

        with connection() as database:
            database.execute("DROP TABLE legacy_income")
            database.execute("DROP TRIGGER sale_item_must_not_be_in_pc")
        with self.assertRaisesRegex(SchemaVersionError, "incompatible"):
            initialize_database()

    def test_live_database_foreign_key_corruption_blocks_startup(self):
        item_id = self.buy("CPU", "CPU", 10)
        with closing(sqlite3.connect(self.database_path)) as database:
            database.execute(
                "INSERT INTO pc_parts (pc_id,expense_id,position) VALUES (999,?,0)",
                (item_id,),
            )
            database.commit()

        with self.assertRaisesRegex(DatabaseIntegrityError, "foreign-key"):
            initialize_database()

    def test_semantically_invalid_current_data_is_rejected(self):
        item_id = self.buy("CPU", "CPU", 10)
        with connection() as database:
            database.execute(
                "UPDATE expenses SET purchase_date='2025-99-99' WHERE id=?",
                (item_id,),
            )

        with self.assertRaisesRegex(DatabaseIntegrityError, "invalid purchase date"):
            initialize_database()
        with self.assertRaisesRegex(sqlite3.DatabaseError, "invalid purchase date"):
            create_backup(Path(self.temporary_directory.name) / "invalid-data-backups")

    def test_purchase_uses_integer_cents_and_iso_dates(self):
        item_id = self.buy("CPU", "cpu", "1,01", date(2026, 8, 13))
        expense = list_expenses()[0]

        self.assertEqual(expense.id, item_id)
        self.assertEqual(expense.item_type, "CPU")
        self.assertEqual(expense.price_cents, 101)
        self.assertEqual(expense.purchase_date, date(2026, 8, 13))
        self.assertTrue(expense.is_available)

        for invalid_price in ("1.999", "1000000000"):
            with (
                self.subTest(invalid_price=invalid_price),
                self.assertRaises(ValidationError),
            ):
                self.buy("Invalid", "Extra", invalid_price)

    def test_purchase_bundle_is_atomic(self):
        with self.assertRaises(ValidationError):
            add_expenses(
                [
                    {"name": "CPU", "item_type": "CPU", "price": 10},
                    {"name": "Bad", "item_type": "Not a component", "price": 5},
                ]
            )
        self.assertEqual(list_expenses(), ())

    def test_assembly_uses_expense_ids_as_its_only_membership_source(self):
        ids = [self.buy("RAM", "RAM", 40), self.buy("RAM", "RAM", 45)]
        pc_id = assemble_pc("PC 1", ids)

        pc = list_pcs()[0]
        self.assertEqual(pc.id, pc_id)
        self.assertEqual(tuple(part.id for part in pc.parts), tuple(ids))
        self.assertEqual(pc.cost_cents, 8500)
        self.assertEqual([item.pc_name for item in list_inventory()], ["PC 1", "PC 1"])

        disassemble_pc(pc_id)
        self.assertTrue(all(item.is_available for item in list_inventory()))

    def test_pc_and_sale_listing_use_constant_query_counts(self):
        pc_parts = [self.buy(f"PC part {index}", "Extra", 10) for index in range(4)]
        assemble_pc("PC 1", pc_parts[:2])
        assemble_pc("PC 2", pc_parts[2:])
        sale_items = [self.buy(f"Sale item {index}", "Extra", 5) for index in range(2)]
        sell_items([sale_items[0]], 10)
        sell_items([sale_items[1]], 10)

        for listing in (list_pcs, list_sales):
            statements = []

            @contextmanager
            def traced_connection():
                with connection() as database:
                    database.set_trace_callback(statements.append)
                    yield database

            with patch("pcims.db.queries.connection", traced_connection):
                self.assertEqual(len(listing()), 2)
            selects = [
                statement
                for statement in statements
                if statement.lstrip().upper().startswith("SELECT")
            ]
            self.assertEqual(len(selects), 2)

    def test_assembly_rolls_back_if_any_item_is_unavailable(self):
        item_id = self.buy("CPU", "CPU", 100)
        with self.assertRaises(NotFoundError):
            assemble_pc("PC 1", [item_id, 9999])
        self.assertEqual(list_pcs(), ())
        self.assertTrue(list_inventory()[0].is_available)

    def test_standalone_group_sale_is_one_record_and_undo_restores_all(self):
        ids = [self.buy("Fan", "Fan", 10) for _ in range(3)]
        sale_id = sell_items(ids, "100.00", date.today())

        sale = list_sales()[0]
        self.assertEqual(sale.id, sale_id)
        self.assertEqual(sale.kind, "item")
        self.assertEqual(sale.cost_cents, 3000)
        self.assertEqual(sale.selling_price_cents, 10000)
        self.assertEqual(sale.profit_cents, 7000)
        self.assertEqual(tuple(item.id for item in sale.items), tuple(ids))
        self.assertEqual(list_inventory(), ())

        undo_sale(sale_id)
        self.assertEqual(list_sales(), ())
        self.assertEqual({item.id for item in list_inventory()}, set(ids))

    def test_pc_sale_and_undo_preserve_duplicate_component_types(self):
        ids = [
            self.buy("CPU", "CPU", 100),
            self.buy("RAM", "RAM", 40),
            self.buy("RAM", "RAM", 45),
        ]
        pc_id = assemble_pc("PC 1", ids)
        sale_id = sell_pc(pc_id, 250, date.today())

        self.assertEqual(list_pcs(), ())
        self.assertEqual(tuple(item.id for item in list_sales()[0].items), tuple(ids))

        undo_sale(sale_id)
        restored = list_pcs()[0]
        self.assertEqual(restored.name, "PC 1")
        self.assertEqual(tuple(item.id for item in restored.parts), tuple(ids))

    def test_pc_undo_name_collision_has_no_partial_effect(self):
        old_id = self.buy("Old CPU", "CPU", 100)
        sale_id = sell_pc(assemble_pc("PC 1", [old_id]), 125)
        new_id = self.buy("New CPU", "CPU", 80)
        assemble_pc("PC 1", [new_id])

        with self.assertRaisesRegex(ValidationError, "PC 1"):
            undo_sale(sale_id)

        self.assertEqual(len(list_sales()), 1)
        self.assertEqual(len(list_pcs()), 1)

    def test_sale_date_before_any_purchase_is_rejected_atomically(self):
        tomorrow = date.today() + timedelta(days=1)
        ids = [self.buy("CPU", "CPU", 100, tomorrow), self.buy("RAM", "RAM", 50)]

        with self.assertRaisesRegex(ValidationError, "purchase date"):
            sell_items(ids, 200, date.today())

        self.assertEqual(list_sales(), ())
        self.assertEqual({item.id for item in list_inventory()}, set(ids))

    def test_delete_and_rename_groups_are_atomic(self):
        ids = [self.buy("Cable", "Extra", 5), self.buy("Cable", "Extra", 6)]
        rename_expenses(ids, "Power Cable")
        self.assertEqual({item.name for item in list_expenses()}, {"Power Cable"})

        assemble_pc("PC 1", [ids[1]])
        with self.assertRaises(ValidationError):
            delete_expenses(ids)
        self.assertEqual({item.id for item in list_expenses()}, set(ids))

    def test_renaming_pc_does_not_rewrite_parts(self):
        item_id = self.buy("CPU", "CPU", 100)
        pc_id = assemble_pc("PC 1", [item_id])
        rename_pc(pc_id, "Workstation")

        pc = list_pcs()[0]
        self.assertEqual(pc.name, "Workstation")
        self.assertEqual(pc.parts[0].name, "CPU")

    def test_financial_summary_uses_current_inventory_and_realized_sales(self):
        sold_id = self.buy("Sold", "Extra", 25)
        self.buy("Stock", "Extra", 40)
        sell_items([sold_id], 50)

        summary = get_financial_summary()
        self.assertEqual(summary.expense_cents, 6500)
        self.assertEqual(summary.income_cents, 5000)
        self.assertEqual(summary.profit_cents, 2500)
        self.assertEqual(summary.inventory_cents, 4000)
        self.assertEqual(summary.cash_flow_cents, -1500)

    def test_verified_backup_restore_and_retention(self):
        self.buy("Old state", "CPU", 10)
        backup_directory = Path(self.temporary_directory.name) / "backups"
        source = create_backup(backup_directory)
        self.buy("New state", "RAM", 20)
        for _ in range(13):
            create_backup(backup_directory)

        safety = restore_backup(source)

        self.assertEqual([item.name for item in list_expenses()], ["Old state"])
        validate_database(safety)
        self.assertLessEqual(len(list(backup_directory.glob("pcims_*.db"))), 14)

    def test_backup_retention_failure_does_not_hide_verified_backup(self):
        self.buy("Keep", "CPU", 10)
        backup_directory = Path(self.temporary_directory.name) / "backups"
        first = create_backup(backup_directory, keep=1)
        original_unlink = Path.unlink

        def fail_for_oldest(path, *args, **kwargs):
            if path == first.path:
                raise PermissionError("simulated locked backup")
            return original_unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", new=fail_for_oldest):
            result = create_backup(backup_directory, keep=1)

        self.assertTrue(result.path.is_file())
        validate_database(result)
        self.assertTrue(result.has_cleanup_warnings)
        self.assertIn("simulated locked backup", result.cleanup_warning)

    def test_restore_rejects_old_or_corrupt_databases_without_changes(self):
        self.buy("Keep me", "CPU", 10)
        invalid = Path(self.temporary_directory.name) / "invalid.db"
        invalid.write_text("not sqlite", encoding="utf-8")

        with self.assertRaises(sqlite3.DatabaseError):
            restore_backup(invalid)
        self.assertEqual([item.name for item in list_expenses()], ["Keep me"])

    def test_backup_rejects_foreign_key_violations(self):
        item_id = self.buy("CPU", "CPU", 10)
        with closing(sqlite3.connect(self.database_path)) as database:
            database.execute(
                "INSERT INTO pc_parts (pc_id,expense_id,position) VALUES (999,?,0)",
                (item_id,),
            )
            database.commit()

        with self.assertRaisesRegex(sqlite3.DatabaseError, "foreign-key"):
            create_backup(Path(self.temporary_directory.name) / "invalid-backups")


if __name__ == "__main__":
    unittest.main()
