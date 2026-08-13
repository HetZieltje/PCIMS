import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from pcims.db.backup import (
    BackupResult,
    create_backup,
    restore_backup,
    validate_database,
)
from pcims.db.connection import Database, configure_database, connection, get_database
from pcims.db.errors import (
    DatabaseIntegrityError,
    NotFoundError,
    SchemaVersionError,
    ValidationError,
)
from pcims.db.queries import (
    ReadQueries,
    add_expenses,
    assemble_pc,
    delete_expenses,
    disassemble_pc,
    get_financial_summary,
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
from pcims.db.schema import SCHEMA_DEFINITIONS, SCHEMA_VERSION, initialize_database
from pcims.money import MAX_MONEY_CENTS
from pcims.services import ApplicationServices

TEST_DATE = date(2026, 8, 14)


class DatabaseWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "pcims-test.db"
        configure_database(self.database_path)
        initialize_database()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_database_configuration_is_an_explicit_immutable_value(self):
        configured = get_database()
        independent = Database.at(Path(self.temporary_directory.name) / "other.db")

        self.assertEqual(configured.path, self.database_path.resolve())
        self.assertNotEqual(configured, independent)
        with independent.transaction() as database:
            database.execute("CREATE TABLE isolated (id INTEGER PRIMARY KEY)")
        self.assertTrue(independent.path.is_file())
        with self.assertRaises(sqlite3.OperationalError), configured.transaction() as database:
            database.execute("SELECT * FROM isolated")

    def test_live_connections_use_durable_wal_and_defensive_pragmas(self):
        with closing(get_database().connect()) as database:
            self.assertEqual(database.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(database.execute("PRAGMA synchronous").fetchone()[0], 2)
            self.assertEqual(database.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(database.execute("PRAGMA trusted_schema").fetchone()[0], 0)

    def test_wal_writer_completes_while_reader_keeps_a_stable_snapshot(self):
        active_database = get_database()
        self.buy("Existing", "Extra", 1)
        with active_database.transaction() as reader:
            before = reader.execute("SELECT COUNT(*) FROM expenses").fetchone()[0]
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    add_expenses,
                    [
                        {
                            "name": "Concurrent",
                            "item_type": "Extra",
                            "price": 1,
                            "purchase_date": TEST_DATE,
                        }
                    ],
                    database=active_database,
                )
                future.result(timeout=2)
            during = reader.execute("SELECT COUNT(*) FROM expenses").fetchone()[0]

        self.assertEqual(before, 1)
        self.assertEqual(during, before)
        self.assertEqual(len(list_expenses()), 2)

    def test_service_snapshot_remains_coherent_during_a_concurrent_write(self):
        expense_id = self.buy("Snapshot CPU", "CPU", 100)
        services = ApplicationServices(get_database())
        original_list_inventory = ReadQueries.list_inventory

        def read_then_assemble(queries, *args, **kwargs):
            inventory = original_list_inventory(queries, *args, **kwargs)
            services.assemble_pc("Concurrent PC", [expense_id])
            return inventory

        with patch.object(
            ReadQueries,
            "list_inventory",
            autospec=True,
            side_effect=read_then_assemble,
        ):
            snapshot = services.inventory_snapshot()

        self.assertEqual([item.id for item in snapshot.inventory], [expense_id])
        self.assertTrue(snapshot.inventory[0].is_available)
        self.assertEqual(snapshot.pcs, ())
        self.assertEqual([pc.name for pc in services.list_pcs()], ["Concurrent PC"])

    def test_transaction_mode_is_explicit_for_reads_and_writes(self):
        active_database = get_database()
        for write, statement in ((False, "BEGIN"), (True, "BEGIN IMMEDIATE")):
            with self.subTest(write=write):
                connection_mock = MagicMock(spec=sqlite3.Connection)
                with patch.object(
                    Database, "connect", return_value=connection_mock
                ), active_database.transaction(write=write) as yielded:
                    self.assertIs(yielded, connection_mock)
                connection_mock.execute.assert_called_once_with(statement)
                connection_mock.commit.assert_called_once()
                connection_mock.close.assert_called_once()

    def test_backup_and_restore_are_serialized_by_the_service_boundary(self):
        services = ApplicationServices(get_database())
        backup_started = threading.Event()
        release_backup = threading.Event()
        restore_started = threading.Event()
        result = BackupResult(self.database_path)

        def slow_backup(*_args, **_kwargs):
            backup_started.set()
            release_backup.wait(2)
            return result

        def observed_restore(*_args, **_kwargs):
            restore_started.set()
            return result

        with (
            patch("pcims.services.create_backup", side_effect=slow_backup),
            patch("pcims.services.restore_backup", side_effect=observed_restore),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            backup_future = executor.submit(services.create_backup)
            self.assertTrue(backup_started.wait(1))
            restore_future = executor.submit(services.restore_backup, self.database_path)
            self.assertFalse(restore_started.wait(0.1))
            release_backup.set()
            self.assertEqual(backup_future.result(timeout=2), result)
            self.assertEqual(restore_future.result(timeout=2), result)
            self.assertTrue(restore_started.is_set())

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
            for table in ("pc_parts", "sale_items"):
                plan = " ".join(
                    row[3]
                    for row in database.execute(
                        f"EXPLAIN QUERY PLAN SELECT * FROM {table} "
                        "WHERE "
                        + ("pc_id" if table == "pc_parts" else "sale_id")
                        + "=1 ORDER BY position"
                    )
                )
                self.assertIn("USING INDEX", plan)
                self.assertNotIn("TEMP B-TREE", plan)

    def test_missing_or_changed_schema_objects_are_rejected(self):
        with connection() as database:
            database.execute("DROP TRIGGER pc_part_must_not_be_sold")
        with self.assertRaisesRegex(SchemaVersionError, "missing"):
            initialize_database()

        with connection() as database:
            database.execute(
                """CREATE TRIGGER pc_part_must_not_be_sold
                   AFTER INSERT ON expenses BEGIN SELECT 1; END"""
            )
        with self.assertRaisesRegex(SchemaVersionError, "changed"):
            initialize_database()

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

    def test_failed_first_run_schema_creation_rolls_back_completely(self):
        partial_path = Path(self.temporary_directory.name) / "partial.db"
        configure_database(partial_path)
        broken = dict(SCHEMA_DEFINITIONS)
        broken[("trigger", "sale_item_must_not_be_in_pc")] = (
            "CREATE TRIGGER broken nonsense"
        )

        with (
            patch("pcims.db.schema.SCHEMA_DEFINITIONS", broken),
            self.assertRaises(sqlite3.OperationalError),
        ):
            initialize_database()

        with closing(sqlite3.connect(partial_path)) as database:
            objects = database.execute(
                "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
            ).fetchall()
            version = database.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(objects, [])
        self.assertEqual(version, 0)

        initialize_database()
        validate_database(partial_path)

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

        for invalid_price in ("1.9999", "1000000000"):
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

    def test_membership_positions_are_unique_within_each_record(self):
        ids = [self.buy("RAM", "RAM", 40), self.buy("RAM", "RAM", 45)]
        pc_id = assemble_pc("PC 1", ids)

        with self.assertRaises(sqlite3.IntegrityError), connection() as database:
            database.execute(
                "UPDATE pc_parts SET position=0 WHERE pc_id=? AND expense_id=?",
                (pc_id, ids[1]),
            )

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
            def traced_connection(_database=None, trace_statements=statements):
                with connection() as database:
                    database.set_trace_callback(trace_statements.append)
                    yield database

            with patch("pcims.db.queries._transaction", traced_connection):
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
        sale_id = sell_items(ids, "100.00", TEST_DATE)

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
        sale_id = sell_pc(pc_id, 250, TEST_DATE)

        self.assertEqual(list_pcs(), ())
        self.assertEqual(tuple(item.id for item in list_sales()[0].items), tuple(ids))

        undo_sale(sale_id)
        restored = list_pcs()[0]
        self.assertEqual(restored.name, "PC 1")
        self.assertEqual(tuple(item.id for item in restored.parts), tuple(ids))

    def test_aggregate_sale_cost_cannot_create_a_self_invalid_database(self):
        price = f"{MAX_MONEY_CENTS // 100}.{MAX_MONEY_CENTS % 100:02d}"
        ids = [self.buy("Expensive", "Extra", price) for _ in range(2)]

        with self.assertRaisesRegex(ValidationError, "Combined item cost"):
            sell_items(ids, 1)

        self.assertEqual(list_sales(), ())
        self.assertTrue(all(item.is_available for item in list_inventory()))

        pc_id = assemble_pc("Expensive PC", ids)
        with self.assertRaisesRegex(ValidationError, "Combined PC cost"):
            sell_pc(pc_id, 1)
        self.assertEqual([pc.id for pc in list_pcs()], [pc_id])
        self.assertEqual(list_sales(), ())
        validate_database(self.database_path)

    def test_pc_names_and_undo_collisions_are_case_insensitive(self):
        original_id = self.buy("Original", "CPU", 100)
        spare_id = self.buy("Spare", "RAM", 50)
        pc_id = assemble_pc("Gaming PC", [original_id])

        with self.assertRaisesRegex(ValidationError, "already exists"):
            assemble_pc(" gaming pc ", [spare_id])

        sale_id = sell_pc(pc_id, 120)
        assemble_pc("GAMING PC", [spare_id])
        with self.assertRaisesRegex(ValidationError, "Cannot undo"):
            undo_sale(sale_id)
        self.assertEqual([pc.name for pc in list_pcs()], ["GAMING PC"])
        self.assertEqual(len(list_sales()), 1)

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
        tomorrow = TEST_DATE + timedelta(days=1)
        ids = [self.buy("CPU", "CPU", 100, tomorrow), self.buy("RAM", "RAM", 50)]

        with self.assertRaisesRegex(ValidationError, "purchase date"):
            sell_items(ids, 200, TEST_DATE)

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

    def test_backup_and_restore_support_uri_special_characters_in_paths(self):
        special_directory = Path(self.temporary_directory.name) / "data # 100% ready"
        special_database = special_directory / "inventory #1%.db"
        configure_database(special_database)
        initialize_database()
        self.buy("Old state", "CPU", 10)
        backup = create_backup(special_directory / "backups #1%")
        self.buy("New state", "RAM", 20)

        restore_backup(backup)

        self.assertEqual([item.name for item in list_expenses()], ["Old state"])
        validate_database(backup)

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

    def test_backup_scan_failure_does_not_hide_verified_backup(self):
        self.buy("Keep", "CPU", 10)
        backup_directory = Path(self.temporary_directory.name) / "backups"

        with patch.object(
            Path, "glob", side_effect=OSError("simulated directory scan failure")
        ):
            result = create_backup(backup_directory)

        self.assertTrue(result.path.is_file())
        validate_database(result)
        self.assertTrue(result.has_cleanup_warnings)
        self.assertIn("simulated directory scan failure", result.cleanup_warning)

    def test_temporary_cleanup_failure_preserves_primary_backup_error(self):
        self.buy("Keep", "CPU", 10)
        backup_directory = Path(self.temporary_directory.name) / "backups"

        with (
            patch(
                "pcims.db.backup.os.replace",
                side_effect=OSError("final replace failed"),
            ),
            patch.object(
                Path, "unlink", side_effect=PermissionError("temporary file locked")
            ),
            self.assertRaisesRegex(OSError, "final replace failed") as raised,
        ):
            create_backup(backup_directory)

        notes = getattr(raised.exception, "__notes__", ())
        self.assertTrue(any("temporary file locked" in note for note in notes))

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
