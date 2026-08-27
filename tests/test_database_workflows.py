import gc
import os
import sqlite3
import stat
import tempfile
import threading
import unittest
import weakref
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from pcims.contracts import BackupResult
from pcims.db.backup import (
    create_backup,
    restore_backup,
    validate_database,
)
from pcims.db.connection import Database, default_database, get_data_dir
from pcims.db.errors import (
    DatabaseIntegrityError,
    NotFoundError,
    SchemaVersionError,
    ValidationError,
)
from pcims.db.expense_commands import add_expenses
from pcims.db.gate import gate_for
from pcims.db.reads import ReadQueries
from pcims.db.schema import (
    SCHEMA_V1_CHECKSUM,
    SCHEMA_V1_DEFINITIONS,
    SCHEMA_VERSION,
    initialize_database,
)
from pcims.domain import ItemDetails, NewExpense, SaleTerms
from pcims.drafts import DraftPurchase, PurchaseDraftStore
from pcims.money import MAX_MONEY_CENTS
from pcims.proofs import NewProof
from pcims.services import ApplicationServices

TEST_DATE = date(2026, 8, 14)


class DatabaseWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "pcims-test.db"
        self.database = Database.at(self.database_path)
        self.services = ApplicationServices(self.database)
        self.services.initialize()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_idle_database_gates_do_not_accumulate_process_global_state(self):
        transient_path = self.database_path.with_name("transient.db")
        transient_gate = gate_for(transient_path)
        gate_reference = weakref.ref(transient_gate)

        del transient_gate
        gc.collect()

        self.assertIsNone(gate_reference())

    @unittest.skipIf(os.name == "nt", "POSIX permission bits are not available")
    def test_database_and_backup_files_are_private_to_the_user(self):
        backup = create_backup(database=self.database)

        self.assertEqual(stat.S_IMODE(self.database_path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(backup.path.stat().st_mode), 0o600)

    @unittest.skipIf(os.name == "nt", "POSIX permission bits are not available")
    def test_default_data_directory_is_private_to_the_user(self):
        data_directory = Path(self.temporary_directory.name) / "private-data"

        with patch.dict(
            os.environ,
            {"PCIMS_DATA_DIR": str(data_directory)},
        ):
            os.environ.pop("PCIMS_DB_PATH", None)
            configured = default_database()

        self.assertEqual(configured.path.parent, data_directory.resolve())
        self.assertEqual(stat.S_IMODE(data_directory.stat().st_mode), 0o700)

    def test_platform_data_directory_ignores_the_other_operating_system(self):
        windows_root = Path(self.temporary_directory.name) / "windows-data"
        linux_root = Path(self.temporary_directory.name) / "linux-data"
        environment = {
            "LOCALAPPDATA": str(windows_root),
            "XDG_DATA_HOME": str(linux_root),
        }

        with (
            patch.dict(os.environ, environment, clear=True),
            patch("pcims.db.connection.OPERATING_SYSTEM", "posix"),
            patch("pcims.db.connection.SYSTEM_PLATFORM", "linux"),
        ):
            self.assertEqual(get_data_dir(), linux_root.resolve() / "pcims")
        with (
            patch.dict(os.environ, environment, clear=True),
            patch("pcims.db.connection.OPERATING_SYSTEM", "nt"),
        ):
            self.assertEqual(get_data_dir(), windows_root.resolve() / "PCIMS")

    def test_macos_uses_native_application_support_directory(self):
        home = Path(self.temporary_directory.name) / "mac-home"
        with (
            patch.dict(
                os.environ,
                {"XDG_DATA_HOME": str(home / "xdg-should-not-be-used")},
                clear=True,
            ),
            patch("pcims.db.connection.OPERATING_SYSTEM", "posix"),
            patch("pcims.db.connection.SYSTEM_PLATFORM", "darwin"),
            patch("pcims.db.connection.Path.home", return_value=home),
        ):
            self.assertEqual(
                get_data_dir(),
                (home / "Library" / "Application Support" / "PCIMS").resolve(),
            )

    def test_relative_environment_database_paths_are_rejected(self):
        with (
            patch.dict(os.environ, {"PCIMS_DB_PATH": "relative.db"}),
            self.assertRaisesRegex(ValueError, "absolute path"),
        ):
            default_database()

    def test_database_configuration_is_an_explicit_immutable_value(self):
        configured = self.database
        independent = Database.at(Path(self.temporary_directory.name) / "other.db")

        self.assertEqual(configured.path, self.database_path.resolve())
        self.assertNotEqual(configured, independent)
        with closing(independent.connect(create=True)) as database:
            database.execute("CREATE TABLE isolated (id INTEGER PRIMARY KEY)")
            database.commit()
        self.assertTrue(independent.path.is_file())
        with (
            self.assertRaises(sqlite3.OperationalError),
            configured.transaction() as database,
        ):
            database.execute("SELECT * FROM isolated")

    def test_connection_is_closed_if_hardening_configuration_fails(self):
        path = Path(self.temporary_directory.name) / "configuration-failure.db"
        path.touch()
        connection = MagicMock()
        connection.execute.side_effect = OSError("pragma rejected")

        with (
            patch("pcims.db.connection.sqlite3.connect", return_value=connection),
            self.assertRaisesRegex(OSError, "pragma rejected"),
        ):
            Database.at(path).connect(create=True)

        connection.close.assert_called_once()

    def test_connection_cleanup_cannot_hide_configuration_failure(self):
        path = Path(self.temporary_directory.name) / "double-failure.db"
        path.touch()
        connection = MagicMock()
        connection.execute.side_effect = OSError("pragma rejected")
        connection.close.side_effect = RuntimeError("close rejected")

        with (
            patch("pcims.db.connection.sqlite3.connect", return_value=connection),
            self.assertRaisesRegex(OSError, "pragma rejected") as raised,
        ):
            Database.at(path).connect(create=True)

        notes = getattr(raised.exception, "__notes__", ())
        self.assertTrue(any("close rejected" in note for note in notes))

    def test_transaction_cleanup_cannot_hide_primary_failure(self):
        connection = MagicMock()
        connection.rollback.side_effect = OSError("rollback rejected")
        connection.close.side_effect = OSError("close rejected")

        with (
            patch.object(Database, "connect", return_value=connection),
            self.assertRaisesRegex(ValueError, "primary failure") as raised,
            self.database.transaction(write=True),
        ):
            raise ValueError("primary failure")

        notes = getattr(raised.exception, "__notes__", ())
        self.assertTrue(any("rollback rejected" in note for note in notes))
        self.assertTrue(any("close rejected" in note for note in notes))

    def test_read_transaction_never_creates_a_missing_database_or_directory(self):
        missing = Database.at(
            Path(self.temporary_directory.name) / "missing-parent" / "missing.db"
        )

        with self.assertRaises(sqlite3.OperationalError), missing.transaction():
            self.fail("A read transaction unexpectedly opened a missing database.")

        self.assertFalse(missing.path.exists())
        self.assertFalse(missing.path.parent.exists())

    def test_live_connections_use_durable_wal_and_defensive_pragmas(self):
        with closing(self.database.connect()) as database:
            self.assertEqual(
                database.execute("PRAGMA journal_mode").fetchone()[0], "wal"
            )
            self.assertEqual(database.execute("PRAGMA synchronous").fetchone()[0], 2)
            self.assertEqual(database.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(database.execute("PRAGMA trusted_schema").fetchone()[0], 0)

    def test_wal_writer_completes_while_reader_keeps_a_stable_snapshot(self):
        active_database = self.database
        self.buy("Existing", "Extra", 1)
        with active_database.transaction() as reader:
            before = reader.execute("SELECT COUNT(*) FROM inventory_items").fetchone()[
                0
            ]
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    add_expenses,
                    [NewExpense.create("Concurrent", "Extra", 1, TEST_DATE)],
                    database=active_database,
                )
                future.result(timeout=2)
            during = reader.execute("SELECT COUNT(*) FROM inventory_items").fetchone()[
                0
            ]

        self.assertEqual(before, 1)
        self.assertEqual(during, before)
        self.assertEqual(len(self.services.list_expenses()), 2)

    def test_service_snapshot_remains_coherent_during_a_concurrent_write(self):
        expense_id = self.buy("Snapshot CPU", "CPU", 100)
        services = ApplicationServices(self.database)
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
        active_database = self.database
        for write, statement in ((False, "BEGIN"), (True, "BEGIN IMMEDIATE")):
            with self.subTest(write=write):
                connection_mock = MagicMock(spec=sqlite3.Connection)
                with (
                    patch.object(Database, "connect", return_value=connection_mock),
                    active_database.transaction(write=write) as yielded,
                ):
                    self.assertIs(yielded, connection_mock)
                connection_mock.execute.assert_called_once_with(statement)
                connection_mock.commit.assert_called_once()
                connection_mock.close.assert_called_once()

    def test_two_direct_restores_are_serialized_by_the_database_gate(self):
        first_restore_started = threading.Event()
        release_first_restore = threading.Event()
        restore_started = threading.Event()
        result = BackupResult(self.database_path)
        call_count = 0
        call_lock = threading.Lock()

        def observed_restore(*_args, **_kwargs):
            nonlocal call_count
            with call_lock:
                call_count += 1
                current_call = call_count
            if current_call == 1:
                first_restore_started.set()
                release_first_restore.wait(2)
            else:
                restore_started.set()
            return result

        with (
            patch("pcims.db.backup._restore_backup", side_effect=observed_restore),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            first = executor.submit(
                restore_backup, self.database_path, database=self.database
            )
            self.assertTrue(first_restore_started.wait(1))
            second = executor.submit(
                restore_backup, self.database_path, database=self.database
            )
            self.assertFalse(restore_started.wait(0.1))
            release_first_restore.set()
            self.assertEqual(first.result(timeout=2), result)
            self.assertEqual(second.result(timeout=2), result)
            self.assertTrue(restore_started.is_set())

    def test_two_direct_backups_are_serialized_through_retention_cleanup(self):
        first_started = threading.Event()
        release_first = threading.Event()
        second_started = threading.Event()
        result = BackupResult(self.database_path)
        call_count = 0
        call_lock = threading.Lock()

        def observed_backup(*_args, **_kwargs):
            nonlocal call_count
            with call_lock:
                call_count += 1
                current_call = call_count
            if current_call == 1:
                first_started.set()
                release_first.wait(2)
            else:
                second_started.set()
            return result

        with (
            patch("pcims.db.backup._create_backup", side_effect=observed_backup),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            first = executor.submit(create_backup, database=self.database)
            self.assertTrue(first_started.wait(1))
            second = executor.submit(create_backup, database=self.database)
            self.assertFalse(second_started.wait(0.1))
            release_first.set()
            self.assertEqual(first.result(timeout=2), result)
            self.assertEqual(second.result(timeout=2), result)

        self.assertTrue(second_started.is_set())

    def test_direct_restore_waits_for_direct_database_transaction(self):
        operation_database = Database.at(self.database_path)
        recovery_database = Database.at(self.database_path)
        operation_started = threading.Event()
        release_operation = threading.Event()
        restore_started = threading.Event()
        result = BackupResult(self.database_path)

        def hold_transaction():
            with operation_database.transaction():
                operation_started.set()
                release_operation.wait(2)

        def observed_restore(*_args, **_kwargs):
            restore_started.set()
            return result

        with (
            patch("pcims.db.backup._restore_backup", side_effect=observed_restore),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            operation = executor.submit(hold_transaction)
            self.assertTrue(operation_started.wait(1))
            recovery = executor.submit(
                restore_backup, self.database_path, database=recovery_database
            )
            self.assertFalse(restore_started.wait(0.1))
            release_operation.set()
            self.assertIsNone(operation.result(timeout=2))
            self.assertEqual(recovery.result(timeout=2), result)
            self.assertTrue(restore_started.is_set())

    def buy(self, name, item_type, price, purchase_date=None):
        purchase_date = TEST_DATE if purchase_date is None else purchase_date
        return self.services.add_expenses(
            [NewExpense.create(name, item_type, price, purchase_date)]
        )[0]

    def test_item_details_are_created_updated_and_searchable(self):
        details = ItemDetails(
            vendor="Retailer",
            serial_number="SN-42",
            storage_location="Shelf A",
            condition="Used",
            warranty_until=date(2028, 1, 2),
            notes="Original box included.",
        )
        expense_id = self.services.add_expenses(
            [NewExpense.create("GPU", "GPU", 250, TEST_DATE, details)]
        )[0]

        stored = self.services.list_expenses()[-1]
        self.assertEqual(stored.details, details)
        replacement_details = ItemDetails(serial_number="SN-43", condition="New")
        self.services.update_expense(
            expense_id,
            NewExpense.create("GPU 2", "GPU", 260, TEST_DATE, replacement_details),
        )
        self.assertEqual(self.services.list_expenses()[-1].details, replacement_details)

    def test_history_searches_purchase_metadata_and_sale_names(self):
        expense_id = self.services.add_expenses(
            [
                NewExpense.create(
                    "Searchable GPU",
                    "GPU",
                    100,
                    TEST_DATE,
                    ItemDetails(serial_number="UNIQUE-SERIAL"),
                )
            ]
        )[0]
        self.services.sell_items([expense_id], SaleTerms.create(120, TEST_DATE))

        by_serial = self.services.sales_snapshot(search="UNIQUE-SERIAL")
        self.assertEqual([item.id for item in by_serial.expenses.records], [expense_id])
        self.assertEqual(by_serial.sales.records, ())
        by_sale = self.services.sales_snapshot(search="Searchable GPU")
        self.assertEqual(by_sale.sales.records[0].name, "Searchable GPU")

    def test_csv_export_contains_stable_ids_metadata_and_sales(self):
        expense_id = self.services.add_expenses(
            [
                NewExpense.create(
                    "Exported",
                    "Extra",
                    10,
                    TEST_DATE,
                    ItemDetails(vendor="Export Shop", serial_number="CSV-1"),
                )
            ]
        )[0]
        self.services.sell_items([expense_id], SaleTerms.create(15, TEST_DATE))
        destination = Path(self.temporary_directory.name) / "export"

        purchases, sales = self.services.export_csv(destination)

        purchases_text = purchases.read_text(encoding="utf-8-sig")
        sales_text = sales.read_text(encoding="utf-8-sig")
        self.assertIn(f"{expense_id},Exported,Extra,10.00", purchases_text)
        self.assertIn("Export Shop,CSV-1", purchases_text)
        self.assertIn("Exported,10.00,15.00,5.00", sales_text)

    def test_purchase_draft_round_trips_details_and_proof_content(self):
        store = PurchaseDraftStore(
            self.database_path,
            Path(self.temporary_directory.name) / "draft-storage",
        )
        proof = NewProof("draft.pdf", "application/pdf", b"%PDF-1.4\n%%EOF")
        line = DraftPurchase(
            7,
            NewExpense.create(
                "Draft GPU",
                "GPU",
                99,
                TEST_DATE,
                ItemDetails(serial_number="DRAFT-1", condition="Used"),
            ),
            (proof,),
        )

        store.save((line,))

        self.assertEqual(store.load(), (line,))
        store.discard()
        self.assertEqual(store.load(), ())

    def test_schema_contains_only_authoritative_current_tables_and_columns(self):
        with self.database.transaction() as database:
            self.assertEqual(
                database.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION
            )
            tables = {
                row[0]
                for row in database.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            }
            item_columns = [
                row[1] for row in database.execute("PRAGMA table_info(inventory_items)")
            ]
            pc_columns = [row[1] for row in database.execute("PRAGMA table_info(pcs)")]
            sale_columns = [
                row[1] for row in database.execute("PRAGMA table_info(sales)")
            ]

        self.assertEqual(
            tables,
            {
                "schema_migrations",
                "inventory_items",
                "pcs",
                "pc_parts",
                "sales",
                "sale_items",
                "proof_files",
                "item_proofs",
                "item_costs",
                "laptops",
                "laptop_slots",
                "laptop_sales",
            },
        )
        self.assertEqual(
            item_columns,
            [
                "id",
                "name",
                "item_type",
                "price_cents",
                "purchase_date",
                "vendor",
                "serial_number",
                "storage_location",
                "condition",
                "warranty_until",
                "notes",
            ],
        )
        self.assertEqual(pc_columns, ["id", "name", "status"])
        self.assertEqual(
            sale_columns,
            ["id", "name", "kind", "pc_id", "selling_price_cents", "sale_date"],
        )

    def test_pc_name_uniqueness_is_enforced_by_unicode_database_collation(self):
        first_id = self.buy("First", "CPU", 10)
        with (
            self.assertRaises(sqlite3.IntegrityError),
            self.database.transaction(write=True) as database,
        ):
            database.execute("INSERT INTO pcs (id,name) VALUES (101,?)", ("Straße",))
            database.execute(
                "INSERT INTO pc_parts (pc_id,item_id,position) VALUES (101,?,0)",
                (first_id,),
            )
            database.execute("INSERT INTO pcs (id,name) VALUES (102,?)", ("STRASSE",))

        with self.database.transaction() as database:
            count = database.execute("SELECT COUNT(*) FROM pcs").fetchone()[0]
        self.assertEqual(count, 0)

    def test_schema_rejects_wrong_types_and_out_of_range_money_directly(self):
        invalid_values = ("not-an-integer", MAX_MONEY_CENTS + 1)
        for invalid in invalid_values:
            with (
                self.subTest(value=invalid),
                self.assertRaises(sqlite3.IntegrityError),
                self.database.transaction(write=True) as database,
            ):
                database.execute(
                    "INSERT INTO inventory_items "
                    "(name,item_type,price_cents,purchase_date) VALUES (?,?,?,?)",
                    ("Invalid", "CPU", invalid, TEST_DATE.isoformat()),
                )

    def test_schema_rejects_impossible_calendar_dates_directly(self):
        for invalid_date in ("2025-02-29", "2025-02-30", "2025-99-99"):
            with (
                self.subTest(date=invalid_date),
                self.assertRaises(sqlite3.IntegrityError),
                self.database.transaction(write=True) as database,
            ):
                database.execute(
                    "INSERT INTO inventory_items "
                    "(name,item_type,price_cents,purchase_date) VALUES (?,?,?,?)",
                    ("Impossible", "CPU", 100, invalid_date),
                )

    def test_schema_rejects_oversized_and_control_character_names_directly(self):
        for invalid_name in ("x" * 201, "CPU\nrenamed"):
            with (
                self.subTest(name=invalid_name),
                self.assertRaises(sqlite3.IntegrityError),
                self.database.transaction(write=True) as database,
            ):
                database.execute(
                    "INSERT INTO inventory_items "
                    "(name,item_type,price_cents,purchase_date) VALUES (?,?,?,?)",
                    (invalid_name, "CPU", 100, TEST_DATE.isoformat()),
                )

    def test_membership_indexes_cover_display_order(self):
        with self.database.transaction() as database:
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

    def test_inventory_index_covers_display_order_without_a_temporary_sort(self):
        with self.database.transaction() as database:
            plan = " ".join(
                row[3]
                for row in database.execute(
                    """EXPLAIN QUERY PLAN
                       SELECT e.id,e.name,e.item_type,e.price_cents,e.purchase_date,
                              p.id AS pc_id,p.name AS pc_name,si.sale_id
                         FROM inventory_items e
                         LEFT JOIN pc_parts pp ON pp.item_id=e.id
                         LEFT JOIN pcs p ON p.id=pp.pc_id
                         LEFT JOIN sale_items si ON si.item_id=e.id
                        WHERE si.sale_id IS NULL
                        ORDER BY e.item_type,e.name COLLATE PCIMS_NOCASE,e.id"""
                )
            )

        self.assertIn("inventory_items_order", plan)
        self.assertNotIn("TEMP B-TREE", plan)

    def test_missing_or_changed_schema_objects_are_rejected(self):
        with self.database.transaction() as database:
            database.execute("DROP TRIGGER pc_part_item_must_be_available")
        with self.assertRaisesRegex(SchemaVersionError, "missing"):
            initialize_database(self.database)

        with self.database.transaction() as database:
            database.execute(
                """CREATE TRIGGER pc_part_item_must_be_available
                   AFTER INSERT ON inventory_items BEGIN SELECT 1; END"""
            )
        with self.assertRaisesRegex(SchemaVersionError, "changed"):
            initialize_database(self.database)

    def test_incompatible_schema_is_rejected_instead_of_mutated(self):
        legacy_path = Path(self.temporary_directory.name) / "legacy.db"
        with closing(sqlite3.connect(legacy_path)) as database:
            database.execute(
                "CREATE TABLE expenses (id INTEGER PRIMARY KEY, price REAL)"
            )
            database.execute("PRAGMA user_version=2")
        legacy_database = Database.at(legacy_path)

        with self.assertRaisesRegex(SchemaVersionError, "incompatible"):
            initialize_database(legacy_database)

        with closing(sqlite3.connect(legacy_path)) as database:
            self.assertEqual(database.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertEqual(
                [row[1] for row in database.execute("PRAGMA table_info(expenses)")],
                ["id", "price"],
            )

    def test_failed_first_run_schema_creation_rolls_back_completely(self):
        partial_path = Path(self.temporary_directory.name) / "partial.db"
        partial_database = Database.at(partial_path)
        broken = dict(SCHEMA_V1_DEFINITIONS)
        broken[("trigger", "sale_item_assignment_valid")] = (
            "CREATE TRIGGER broken nonsense"
        )

        with (
            patch("pcims.db.schema.SCHEMA_V1_DEFINITIONS", broken),
            self.assertRaises(sqlite3.OperationalError),
        ):
            initialize_database(partial_database)

        with closing(sqlite3.connect(partial_path)) as database:
            objects = database.execute(
                "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
            ).fetchall()
            version = database.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(objects, [])
        self.assertEqual(version, 0)

        initialize_database(partial_database)
        validate_database(partial_path)

    def test_v1_baseline_is_backed_up_and_migrated_without_losing_inventory(self):
        migration_path = Path(self.temporary_directory.name) / "migration.db"
        migration_database = Database.at(migration_path)
        with closing(migration_database.connect(create=True)) as database:
            for statement in SCHEMA_V1_DEFINITIONS.values():
                database.execute(statement)
            database.execute(
                """INSERT INTO schema_migrations
                   (version,name,checksum,applied_at) VALUES (1,?,?,?)""",
                (
                    "initial inventory baseline",
                    SCHEMA_V1_CHECKSUM,
                    "2026-08-14T12:00:00Z",
                ),
            )
            database.execute("PRAGMA user_version=1")
            database.execute(
                """INSERT INTO inventory_items
                   (name,item_type,price_cents,purchase_date)
                   VALUES ('Migrated CPU','CPU',10000,'2026-08-14')"""
            )
            database.execute(
                """INSERT INTO activity_events
                   (occurred_at,action,entity_type,entity_id,summary)
                   VALUES ('2026-08-14T12:00:00Z','created','item',1,'Old event')"""
            )
            database.commit()

        initialize_database(migration_database)

        with migration_database.transaction() as database:
            tables = {
                row[0]
                for row in database.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            markers = database.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
            name = database.execute(
                "SELECT name FROM inventory_items WHERE id=1"
            ).fetchone()[0]
        self.assertEqual(name, "Migrated CPU")
        self.assertEqual([row[0] for row in markers], [1, 2, 3])
        self.assertNotIn("activity_events", tables)
        backups = tuple((migration_path.parent / "backups").glob("pcims_*.db"))
        self.assertEqual(len(backups), 1)
        with closing(sqlite3.connect(backups[0])) as backup:
            self.assertEqual(backup.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(
                backup.execute("SELECT summary FROM activity_events").fetchone()[0],
                "Old event",
            )

    def test_current_version_with_wrong_layout_is_rejected(self):
        with self.database.transaction() as database:
            database.execute(
                "ALTER TABLE inventory_items RENAME COLUMN price_cents TO price_value"
            )

        with self.assertRaisesRegex(SchemaVersionError, "incompatible"):
            initialize_database(self.database)

    def test_current_schema_rejects_extra_tables_and_missing_triggers(self):
        with self.database.transaction() as database:
            database.execute("CREATE TABLE legacy_income (id INTEGER PRIMARY KEY)")
        with self.assertRaisesRegex(SchemaVersionError, "incompatible"):
            initialize_database(self.database)

        with self.database.transaction() as database:
            database.execute("DROP TABLE legacy_income")
            database.execute("DROP TRIGGER sale_item_assignment_valid")
        with self.assertRaisesRegex(SchemaVersionError, "incompatible"):
            initialize_database(self.database)

    def test_live_database_foreign_key_corruption_blocks_startup(self):
        item_id = self.buy("CPU", "CPU", 10)
        with closing(sqlite3.connect(self.database_path)) as database:
            database.execute(
                "INSERT INTO pc_parts (pc_id,item_id,position) VALUES (999,?,0)",
                (item_id,),
            )
            database.commit()

        with self.assertRaisesRegex(DatabaseIntegrityError, "foreign-key"):
            initialize_database(self.database)

    def test_forced_date_corruption_is_rejected(self):
        item_id = self.buy("CPU", "CPU", 10)
        with closing(self.database.connect()) as database:
            database.execute("PRAGMA ignore_check_constraints=ON")
            database.execute(
                "UPDATE inventory_items SET purchase_date='2025-99-99' WHERE id=?",
                (item_id,),
            )
            database.commit()

        with self.assertRaisesRegex(DatabaseIntegrityError, "CHECK constraint"):
            initialize_database(self.database)
        with self.assertRaisesRegex(sqlite3.DatabaseError, "invalid purchase date"):
            create_backup(
                Path(self.temporary_directory.name) / "invalid-data-backups",
                database=self.database,
            )

    def test_purchase_uses_integer_cents_and_iso_dates(self):
        item_id = self.buy("CPU", "cpu", "1,01", date(2026, 8, 13))
        expense = self.services.list_expenses()[0]

        self.assertEqual(expense.id, item_id)
        self.assertEqual(expense.item_type, "CPU")
        self.assertEqual(expense.price_cents, 101)
        self.assertEqual(expense.purchase_date, date(2026, 8, 13))
        self.assertTrue(expense.is_available)

        for invalid_price in ("1.9999", "1000000000"):
            with (
                self.subTest(invalid_price=invalid_price),
                self.assertRaises(ValueError),
            ):
                self.buy("Invalid", "Extra", invalid_price)

    def test_purchase_name_snapshot_reads_only_distinct_names(self):
        self.buy("Alpha", "CPU", 10)
        self.buy("Alpha", "RAM", 20)
        self.buy("beta", "Extra", 5)

        with patch(
            "pcims.db.reads.expense_from_row",
            side_effect=AssertionError("full expense mapping is unnecessary"),
        ):
            snapshot = self.services.purchases_snapshot()

        self.assertEqual(snapshot.expense_names, ("Alpha", "beta"))

    def test_purchase_bundle_is_atomic(self):
        with self.assertRaises(ValueError):
            self.services.add_expenses(
                [
                    NewExpense.create("CPU", "CPU", 10),
                    NewExpense.create("Bad", "Not a component", 5),
                ]
            )
        self.assertEqual(self.services.list_expenses(), ())

    def test_purchase_proof_is_shared_without_duplicate_blob_storage(self):
        proof = NewProof(
            "receipt.pdf",
            "application/pdf",
            b"%PDF-1.7\nPCIMS test receipt",
        )
        items = [
            NewExpense.create(f"RAM {index}", "RAM", 20, TEST_DATE)
            for index in range(3)
        ]

        identifiers = self.services.add_expenses(items, [(proof,)] * 3)
        expenses = self.services.list_expenses()

        self.assertEqual([item.id for item in expenses], identifiers)
        self.assertTrue(all(len(item.proofs) == 1 for item in expenses))
        self.assertEqual({item.proofs[0].id for item in expenses}, {1})
        self.assertEqual(self.services.proof_file(identifiers[0], 1), proof)
        with self.database.transaction() as database:
            self.assertEqual(
                database.execute("SELECT COUNT(*) FROM proof_files").fetchone()[0],
                1,
            )
            self.assertEqual(
                database.execute("SELECT COUNT(*) FROM item_proofs").fetchone()[0],
                3,
            )
        self.services.delete_expenses(identifiers)
        with self.database.transaction() as database:
            self.assertEqual(
                database.execute("SELECT COUNT(*) FROM proof_files").fetchone()[0],
                0,
            )

    def test_proof_content_is_deduplicated_independently_of_attachment_name(self):
        content = b"%PDF-1.4\nsame receipt content"
        first = NewProof("invoice.pdf", "application/pdf", content)
        second = NewProof("renamed-invoice.pdf", "application/pdf", content)
        item_ids = self.services.add_expenses(
            [
                NewExpense.create("CPU", "CPU", 10, TEST_DATE),
                NewExpense.create("RAM", "RAM", 10, TEST_DATE),
            ],
            [(first,), (second,)],
        )

        with self.database.transaction() as database:
            self.assertEqual(
                database.execute("SELECT COUNT(*) FROM proof_files").fetchone()[0], 1
            )
            names = [
                row[0]
                for row in database.execute(
                    "SELECT file_name FROM item_proofs ORDER BY item_id"
                )
            ]
        self.assertEqual(names, ["invoice.pdf", "renamed-invoice.pdf"])
        self.assertEqual(
            self.services.proof_file(item_ids[1], 1).file_name,
            "renamed-invoice.pdf",
        )

    def test_proofs_can_be_replaced_after_sale_and_orphans_are_removed(self):
        pdf = NewProof("invoice.pdf", "application/pdf", b"%PDF-1.4\ninvoice")
        png = NewProof(
            "payment.png",
            "image/png",
            b"\x89PNG\r\n\x1a\nproof bytes",
        )
        first, second = self.services.add_expenses(
            [
                NewExpense.create("CPU", "CPU", 50, TEST_DATE),
                NewExpense.create("GPU", "GPU", 100, TEST_DATE),
            ],
            [(pdf,), (pdf,)],
        )
        self.services.sell_items([first], SaleTerms.create(75, TEST_DATE))

        self.services.replace_expense_proofs(first, (), (png,))
        self.assertEqual(
            [proof.file_name for proof in self.services.list_expenses()[0].proofs],
            ["payment.png"],
        )
        self.services.replace_expense_proofs(second, (), ())

        with self.database.transaction() as database:
            names = [
                row[0]
                for row in database.execute(
                    "SELECT file_name FROM item_proofs ORDER BY file_name"
                )
            ]
        self.assertEqual(names, ["payment.png"])

    def test_purchase_proof_mapping_is_validated_before_any_write(self):
        proof = NewProof("receipt.pdf", "application/pdf", b"%PDF-1.4\nreceipt")
        with self.assertRaisesRegex(ValidationError, "proof collection"):
            self.services.add_expenses(
                [
                    NewExpense.create("CPU", "CPU", 50, TEST_DATE),
                    NewExpense.create("RAM", "RAM", 20, TEST_DATE),
                ],
                [(proof,)],
            )
        self.assertEqual(self.services.list_expenses(), ())

    def test_verified_backup_and_restore_preserve_proof_content(self):
        proof = NewProof("receipt.pdf", "application/pdf", b"%PDF-1.5\nreceipt")
        expense_id = self.services.add_expenses(
            [NewExpense.create("PSU", "PSU", 60, TEST_DATE)],
            [(proof,)],
        )[0]
        backup = self.services.create_backup()

        self.services.replace_expense_proofs(expense_id, (), ())
        self.assertEqual(self.services.list_expenses()[0].proofs, ())
        self.services.restore_backup(backup.path)

        restored = self.services.list_expenses()[0]
        self.assertEqual([item.file_name for item in restored.proofs], ["receipt.pdf"])
        self.assertEqual(
            self.services.proof_file(expense_id, restored.proofs[0].id), proof
        )

    def test_assembly_uses_expense_ids_as_its_only_membership_source(self):
        ids = [self.buy("RAM", "RAM", 40), self.buy("RAM", "RAM", 45)]
        pc_id = self.services.assemble_pc("PC 1", ids)

        pc = self.services.list_pcs()[0]
        self.assertEqual(pc.id, pc_id)
        self.assertEqual(tuple(part.id for part in pc.parts), tuple(ids))
        self.assertEqual(pc.cost_cents, 8500)
        self.assertEqual(
            [item.pc_name for item in self.services.list_inventory()],
            ["PC 1", "PC 1"],
        )

        self.services.disassemble_pc(pc_id)
        self.assertTrue(
            all(item.is_available for item in self.services.list_inventory())
        )

    def test_membership_positions_are_unique_within_each_record(self):
        ids = [self.buy("RAM", "RAM", 40), self.buy("RAM", "RAM", 45)]
        pc_id = self.services.assemble_pc("PC 1", ids)

        with (
            self.assertRaises(sqlite3.IntegrityError),
            self.database.transaction() as database,
        ):
            database.execute(
                "UPDATE pc_parts SET position=0 WHERE pc_id=? AND item_id=?",
                (pc_id, ids[1]),
            )

    def test_published_record_ids_are_never_reused(self):
        first_expense = self.buy("First expense", "Extra", 1)
        self.services.delete_expenses([first_expense])
        second_expense = self.buy("Second expense", "Extra", 1)

        first_pc = self.services.assemble_pc("First PC", [second_expense])
        self.services.disassemble_pc(first_pc)
        second_pc = self.services.assemble_pc("Second PC", [second_expense])
        self.services.disassemble_pc(second_pc)

        first_sale = self.services.sell_items(
            [second_expense], SaleTerms.create(2, TEST_DATE)
        )
        self.services.undo_sale(first_sale)
        second_sale = self.services.sell_items(
            [second_expense], SaleTerms.create(2, TEST_DATE)
        )

        self.assertGreater(second_expense, first_expense)
        self.assertGreater(second_pc, first_pc)
        self.assertGreater(second_sale, first_sale)

    def test_pc_and_sale_listing_use_constant_query_counts(self):
        pc_parts = [self.buy(f"PC part {index}", "Extra", 10) for index in range(4)]
        self.services.assemble_pc("PC 1", pc_parts[:2])
        self.services.assemble_pc("PC 2", pc_parts[2:])
        sale_items = [self.buy(f"Sale item {index}", "Extra", 5) for index in range(2)]
        self.services.sell_items([sale_items[0]], SaleTerms.create(10))
        self.services.sell_items([sale_items[1]], SaleTerms.create(10))

        for listing in (self.services.list_pcs, self.services.list_sales):
            statements = []
            original_connect = Database.connect

            def traced_connect(
                database,
                trace_statements=statements,
                connect=original_connect,
            ):
                connection = connect(database)
                connection.set_trace_callback(trace_statements.append)
                return connection

            with patch.object(Database, "connect", new=traced_connect):
                self.assertEqual(len(listing()), 2)
            selects = [
                statement
                for statement in statements
                if statement.lstrip().upper().startswith("SELECT")
            ]
            self.assertEqual(len(selects), 3)

    def test_all_read_projections_agree_on_a_mixed_inventory_state(self):
        available = self.buy("Available", "Extra", 10)
        current_parts = [
            self.buy("Current CPU", "CPU", 20),
            self.buy("Current RAM", "RAM", 30),
        ]
        sold_item = self.buy("Sold item", "Extra", 40)
        sold_pc_parts = [
            self.buy("Sold GPU", "GPU", 50),
            self.buy("Sold PSU", "PSU", 60),
        ]
        current_pc = self.services.assemble_pc("Current PC", current_parts)
        sold_pc = self.services.assemble_pc("Sold PC", sold_pc_parts)
        item_sale = self.services.sell_items(
            [sold_item], SaleTerms.create(45, TEST_DATE)
        )
        pc_sale = self.services.sell_pc(sold_pc, SaleTerms.create(120, TEST_DATE))

        expenses = self.services.list_expenses()
        inventory = self.services.list_inventory()
        available_inventory = self.services.list_inventory(available_only=True)
        pcs = self.services.list_pcs()
        sales = self.services.list_sales()
        snapshot = self.services.sales_snapshot(page_size=10)
        summary = self.services.financial_summary()

        self.assertEqual(len(expenses), 6)
        self.assertEqual({item.id for item in inventory}, {available, *current_parts})
        self.assertEqual([item.id for item in available_inventory], [available])
        self.assertEqual([pc.id for pc in pcs], [current_pc])
        self.assertEqual([part.id for part in pcs[0].parts], current_parts)
        self.assertEqual([sale.id for sale in sales], [item_sale, pc_sale])
        self.assertEqual(
            [sale.id for sale in snapshot.sales.records], [pc_sale, item_sale]
        )
        self.assertEqual([sale.item_count for sale in snapshot.sales.records], [2, 1])
        self.assertEqual(
            [item.id for item in self.services.sale_item_page(pc_sale).records],
            sold_pc_parts,
        )
        self.assertEqual(summary.expense_cents, 21_000)
        self.assertEqual(summary.income_cents, 16_500)
        self.assertEqual(summary.profit_cents, 1_500)
        self.assertEqual(summary.realized_cost_cents, 15_000)
        self.assertEqual(summary.roi_basis_points, 1_000)
        self.assertEqual(summary.inventory_cents, 6_000)
        self.assertEqual(snapshot.summary, summary)

    def test_assembly_rolls_back_if_any_item_is_unavailable(self):
        item_id = self.buy("CPU", "CPU", 100)
        with self.assertRaises(NotFoundError):
            self.services.assemble_pc("PC 1", [item_id, 9999])
        self.assertEqual(self.services.list_pcs(), ())
        self.assertTrue(self.services.list_inventory()[0].is_available)

    def test_standalone_group_sale_is_one_record_and_undo_restores_all(self):
        ids = [self.buy("Fan", "Fan", 10) for _ in range(3)]
        sale_id = self.services.sell_items(ids, SaleTerms.create("100.00", TEST_DATE))

        sale = self.services.list_sales()[0]
        self.assertEqual(sale.id, sale_id)
        self.assertEqual(sale.kind, "item")
        self.assertEqual(sale.cost_cents, 3000)
        self.assertEqual(sale.selling_price_cents, 10000)
        self.assertEqual(sale.profit_cents, 7000)
        self.assertEqual(sale.roi_basis_points, 23_333)
        self.assertEqual(tuple(item.id for item in sale.items), tuple(ids))
        self.assertEqual(self.services.list_inventory(), ())

        self.services.undo_sale(sale_id)
        self.assertEqual(self.services.list_sales(), ())
        self.assertEqual({item.id for item in self.services.list_inventory()}, set(ids))

    def test_sale_terms_can_be_corrected_without_replacing_the_sale(self):
        item_id = self.buy("Correctable", "Extra", 100)
        sale_id = self.services.sell_items(
            [item_id], SaleTerms.create(150, TEST_DATE + timedelta(days=1))
        )

        self.services.update_sale(
            sale_id, SaleTerms.create(125, TEST_DATE + timedelta(days=2))
        )

        sale = self.services.list_sales()[0]
        self.assertEqual(sale.id, sale_id)
        self.assertEqual([item.id for item in sale.items], [item_id])
        self.assertEqual(sale.selling_price_cents, 12_500)
        self.assertEqual(sale.sale_date, TEST_DATE + timedelta(days=2))
        self.assertEqual(sale.profit_cents, 2_500)

    def test_invalid_sale_correction_rolls_back(self):
        item_id = self.buy("Correctable", "Extra", 100)
        sale_id = self.services.sell_items([item_id], SaleTerms.create(150, TEST_DATE))

        with self.assertRaisesRegex(ValidationError, "before purchase"):
            self.services.update_sale(
                sale_id, SaleTerms.create(90, TEST_DATE - timedelta(days=1))
            )

        sale = self.services.list_sales()[0]
        self.assertEqual(sale.selling_price_cents, 15_000)
        self.assertEqual(sale.sale_date, TEST_DATE)

    def test_missing_sale_cannot_be_edited(self):
        with self.assertRaisesRegex(NotFoundError, "does not exist"):
            self.services.update_sale(999, SaleTerms.create(10, TEST_DATE))

    def test_roi_handles_losses_and_zero_cost_sales(self):
        loss_item = self.buy("Loss", "Extra", 100)
        free_item = self.buy("Free", "Extra", 0)
        self.services.sell_items([loss_item], SaleTerms.create(75, TEST_DATE))
        self.services.sell_items([free_item], SaleTerms.create(25, TEST_DATE))

        loss_sale, free_sale = self.services.list_sales()
        self.assertEqual(loss_sale.roi_basis_points, -2_500)
        self.assertIsNone(free_sale.roi_basis_points)

        summaries = self.services.sales_snapshot(page_size=10).sales.records
        self.assertIsNone(summaries[0].roi_basis_points)
        self.assertEqual(summaries[1].roi_basis_points, -2_500)

    def test_financial_roi_is_unavailable_when_sold_items_have_no_cost(self):
        free_item = self.buy("Free", "Extra", 0)
        self.services.sell_items([free_item], SaleTerms.create(25, TEST_DATE))

        summary = self.services.financial_summary()
        self.assertEqual(summary.realized_cost_cents, 0)
        self.assertEqual(summary.profit_cents, 2_500)
        self.assertIsNone(summary.roi_basis_points)

    def test_total_proof_storage_limit_is_reported_before_insertion(self):
        proof = NewProof("receipt.pdf", "application/pdf", b"%PDF-1.4\nreceipt")
        with (
            patch("pcims.db.expense_commands.MAX_TOTAL_PROOF_BYTES", 5),
            self.assertRaisesRegex(ValidationError, "512 MiB"),
        ):
            self.services.add_expenses(
                [NewExpense.create("Limited", "Extra", 1, TEST_DATE)],
                [(proof,)],
            )
        self.assertEqual(self.services.list_expenses(), ())

    def test_storage_summary_counts_deduplicated_proofs_and_backups(self):
        proof = NewProof("receipt.pdf", "application/pdf", b"%PDF-1.4\nreceipt")
        self.services.add_expenses(
            [
                NewExpense.create("One", "Extra", 1, TEST_DATE),
                NewExpense.create("Two", "Extra", 1, TEST_DATE),
            ],
            [(proof,), (proof,)],
        )
        backup = self.services.create_backup()

        summary = self.services.storage_summary()
        self.assertGreater(summary.database_bytes, 0)
        self.assertEqual(summary.proof_count, 1)
        self.assertEqual(summary.proof_bytes, len(proof.content))
        self.assertEqual(summary.backup_count, 1)
        self.assertEqual(summary.backup_bytes, backup.path.stat().st_size)

    def test_sale_summaries_and_item_details_are_independently_bounded(self):
        ids = self.services.add_expenses(
            NewExpense.create(f"Bulk item {index}", "Extra", 1, TEST_DATE)
            for index in range(1_001)
        )
        sale_id = self.services.sell_items(ids, SaleTerms.create(2_000, TEST_DATE))

        snapshot = self.services.sales_snapshot(page_size=1)
        sale = snapshot.sales.records[0]
        first = self.services.sale_item_page(sale_id, page_size=500)
        last = self.services.sale_item_page(sale_id, 1_000, 500)

        self.assertEqual(sale.item_count, 1_001)
        self.assertEqual(sale.cost_cents, 100_100)
        self.assertFalse(hasattr(sale, "items"))
        self.assertEqual(len(first.records), 500)
        self.assertTrue(first.has_next)
        self.assertEqual(last.offset, 1_000)
        self.assertEqual(len(last.records), 1)
        self.assertFalse(last.has_next)

        for arguments in (
            (sale_id, 0, 0),
            (0, 0, 500),
            (sale_id, -1, 500),
            (sale_id, 0, True),
            (sale_id, 1.5, 500),
        ):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                self.services.sale_item_page(*arguments)
        with self.assertRaisesRegex(NotFoundError, "does not exist"):
            self.services.sale_item_page(sale_id + 1)

    def test_sales_history_is_bounded_navigable_and_clamped(self):
        ids = [self.buy(f"History {index}", "Extra", 1) for index in range(7)]
        for item_id in ids:
            self.services.sell_items([item_id], SaleTerms.create(2))

        newest = self.services.sales_snapshot(page_size=3)
        older = self.services.sales_snapshot(3, 3, 3)
        clamped = self.services.sales_snapshot(999, 999, 3)

        self.assertEqual(newest.expenses.total, 7)
        self.assertEqual([item.id for item in newest.expenses.records], [7, 6, 5])
        self.assertEqual([sale.id for sale in newest.sales.records], [7, 6, 5])
        self.assertFalse(newest.expenses.has_previous)
        self.assertTrue(newest.expenses.has_next)
        self.assertEqual([item.id for item in older.expenses.records], [4, 3, 2])
        self.assertTrue(older.sales.has_previous)
        self.assertTrue(older.sales.has_next)
        self.assertEqual(clamped.expenses.offset, 6)
        self.assertEqual([item.id for item in clamped.expenses.records], [1])
        self.assertFalse(clamped.sales.has_next)

        for arguments in (
            (0, 0, 0),
            (-1, 0, 3),
            (0, -1, 3),
            (0, 0, True),
            (1.5, 0, 3),
        ):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                self.services.sales_snapshot(*arguments)

    def test_financial_summary_uses_one_select_round_trip(self):
        statements: list[str] = []
        original_connect = Database.connect

        def traced_connect(database, *args, **kwargs):
            connection = original_connect(database, *args, **kwargs)
            connection.set_trace_callback(statements.append)
            return connection

        with patch.object(Database, "connect", new=traced_connect):
            self.services.financial_summary()

        selects = [
            statement
            for statement in statements
            if statement.lstrip().upper().startswith("SELECT")
        ]
        self.assertEqual(len(selects), 1)

    def test_sold_items_can_be_corrected_and_sale_reads_current_item_data(self):
        item_id = self.buy("Historical CPU", "CPU", 30)
        self.services.sell_items([item_id], SaleTerms.create(50))

        self.services.update_expense(
            item_id,
            NewExpense.create("Corrected CPU", "CPU", 40, TEST_DATE),
        )

        sale = self.services.list_sales()[0]
        self.assertEqual(sale.items[0].name, "Corrected CPU")
        self.assertEqual(sale.cost_cents, 4_000)

    def test_sold_item_correction_cannot_overflow_combined_sale_cost(self):
        first_id = self.buy(
            "Maximum less one", "Extra", f"{(MAX_MONEY_CENTS - 1) / 100:.2f}"
        )
        second_id = self.buy("One cent", "Extra", "0.01")
        self.services.sell_items([first_id, second_id], SaleTerms.create(1, TEST_DATE))

        with self.assertRaisesRegex(ValidationError, "Combined sale cost"):
            self.services.update_expense(
                second_id,
                NewExpense.create("Two cents", "Extra", "0.02", TEST_DATE),
            )
        self.assertEqual(
            next(
                item for item in self.services.list_expenses() if item.id == second_id
            ).price_cents,
            1,
        )

    def test_pc_sale_and_undo_preserve_duplicate_component_types(self):
        ids = [
            self.buy("CPU", "CPU", 100),
            self.buy("RAM", "RAM", 40),
            self.buy("RAM", "RAM", 45),
        ]
        pc_id = self.services.assemble_pc("PC 1", ids)
        sale_id = self.services.sell_pc(pc_id, SaleTerms.create(250, TEST_DATE))

        self.assertEqual(self.services.list_pcs(), ())
        self.assertEqual(
            tuple(item.id for item in self.services.list_sales()[0].items), tuple(ids)
        )

        self.services.undo_sale(sale_id)
        restored = self.services.list_pcs()[0]
        self.assertEqual(restored.name, "PC 1")
        self.assertEqual(tuple(item.id for item in restored.parts), tuple(ids))

    def test_aggregate_sale_cost_cannot_create_a_self_invalid_database(self):
        price = f"{MAX_MONEY_CENTS // 100}.{MAX_MONEY_CENTS % 100:02d}"
        ids = [self.buy("Expensive", "Extra", price) for _ in range(2)]

        with self.assertRaisesRegex(ValidationError, "Combined item cost"):
            self.services.sell_items(ids, SaleTerms.create(1))

        self.assertEqual(self.services.list_sales(), ())
        self.assertTrue(
            all(item.is_available for item in self.services.list_inventory())
        )

        with self.assertRaisesRegex(ValidationError, "Combined PC cost"):
            self.services.assemble_pc("Expensive PC", ids)
        self.assertEqual(self.services.list_pcs(), ())
        self.assertEqual(self.services.list_sales(), ())
        validate_database(self.database_path)

    def test_database_rules_keep_pc_and_sale_assignments_consistent(self):
        component_id = self.buy("PC part", "CPU", 100)
        spare_id = self.buy("Spare", "RAM", 40)
        pc_id = self.services.assemble_pc("Rules PC", [component_id])

        with (
            self.assertRaisesRegex(sqlite3.IntegrityError, "does not belong"),
            self.database.transaction(write=True) as database,
        ):
            sale_id = database.execute(
                """INSERT INTO sales
                   (name,kind,pc_id,selling_price_cents,sale_date)
                   VALUES ('Invalid item sale','item',NULL,100,?)""",
                (TEST_DATE.isoformat(),),
            ).lastrowid
            database.execute(
                "INSERT INTO sale_items (sale_id,item_id,position) VALUES (?,?,0)",
                (sale_id, component_id),
            )

        sale_id = self.services.sell_pc(pc_id, SaleTerms.create(150, TEST_DATE))
        with (
            self.assertRaisesRegex(sqlite3.IntegrityError, "sold PC membership"),
            self.database.transaction(write=True) as database,
        ):
            database.execute("DELETE FROM pc_parts WHERE pc_id=?", (pc_id,))
        with (
            self.assertRaisesRegex(sqlite3.IntegrityError, "sold PC membership"),
            self.database.transaction(write=True) as database,
        ):
            database.execute(
                "INSERT INTO pc_parts (pc_id,item_id,position) VALUES (?,?,1)",
                (pc_id, spare_id),
            )

        self.services.undo_sale(sale_id)
        self.services.update_pc(pc_id, "Rules PC corrected", [component_id, spare_id])
        self.assertEqual(
            [part.id for part in self.services.list_pcs()[0].parts],
            [component_id, spare_id],
        )

    def test_semantic_validation_rejects_empty_aggregate_records(self):
        with self.database.transaction(write=True) as database:
            database.execute("INSERT INTO pcs (name) VALUES ('Empty PC')")
        with self.assertRaisesRegex(DatabaseIntegrityError, "has no components"):
            initialize_database(self.database)
        with self.database.transaction(write=True) as database:
            database.execute("DELETE FROM pcs WHERE name='Empty PC'")

        with self.database.transaction(write=True) as database:
            database.execute(
                """INSERT INTO sales
                   (name,kind,pc_id,selling_price_cents,sale_date)
                   VALUES ('Empty sale','item',NULL,100,?)""",
                (TEST_DATE.isoformat(),),
            )
        with self.assertRaisesRegex(DatabaseIntegrityError, "invalid items"):
            initialize_database(self.database)

    def test_linked_items_and_active_pc_memberships_are_correctable(self):
        first_id = self.buy("Linked CPU", "CPU", 10)
        second_id = self.buy("Linked RAM", "RAM", 5)
        pc_id = self.services.assemble_pc("Correctable PC", [first_id])

        self.services.update_expense(
            first_id,
            NewExpense.create("Corrected CPU", "CPU", 12, TEST_DATE),
        )
        self.services.update_pc(pc_id, "Corrected PC", [first_id, second_id])
        sale_id = self.services.sell_pc(pc_id, SaleTerms.create(25, TEST_DATE))
        self.services.update_expense(
            second_id,
            NewExpense.create("Corrected RAM", "RAM", 6, TEST_DATE),
        )
        self.services.undo_sale(sale_id)

        restored = self.services.list_pcs()[0]
        self.assertEqual(restored.id, pc_id)
        self.assertEqual(restored.name, "Corrected PC")
        self.assertEqual(
            [part.name for part in restored.parts], ["Corrected CPU", "Corrected RAM"]
        )

    def test_component_edit_replaces_every_field_inside_an_assembled_pc(self):
        first_ram = self.buy("RAM A", "RAM", 40)
        second_ram = self.buy("RAM B", "RAM", 45)
        pc_id = self.services.assemble_pc("Dual RAM PC", [first_ram, second_ram])
        replacement_date = TEST_DATE - timedelta(days=10)

        self.services.update_expense(
            first_ram,
            NewExpense.create("Primary GPU", "GPU", 125, replacement_date),
        )

        pc = self.services.list_pcs()[0]
        edited = next(part for part in pc.parts if part.id == first_ram)
        self.assertEqual(pc.id, pc_id)
        self.assertEqual(pc.name, "Dual RAM PC")
        self.assertEqual(tuple(part.id for part in pc.parts), (first_ram, second_ram))
        self.assertEqual(edited.name, "Primary GPU")
        self.assertEqual(edited.item_type, "GPU")
        self.assertEqual(edited.price_cents, 12_500)
        self.assertEqual(edited.purchase_date, replacement_date)
        self.assertEqual(pc.cost_cents, 17_000)

    def test_pc_edit_replaces_name_and_membership_with_duplicate_types(self):
        first_ram = self.buy("RAM A", "RAM", 40)
        removed_ram = self.buy("RAM B", "RAM", 45)
        added_ram = self.buy("RAM C", "RAM", 50)
        other_part = self.buy("Other PC CPU", "CPU", 80)
        pc_id = self.services.assemble_pc("Original PC", [first_ram, removed_ram])
        other_pc_id = self.services.assemble_pc("Other PC", [other_part])

        self.services.update_pc(
            pc_id,
            "Three-stick PC",
            [first_ram, added_ram, removed_ram],
        )

        pc = next(pc for pc in self.services.list_pcs() if pc.id == pc_id)
        self.assertEqual(pc.name, "Three-stick PC")
        self.assertEqual(
            tuple(part.id for part in pc.parts),
            (first_ram, added_ram, removed_ram),
        )
        self.assertEqual([part.item_type for part in pc.parts], ["RAM", "RAM", "RAM"])
        with self.database.transaction() as connection:
            memberships = connection.execute(
                """SELECT item_id,position FROM pc_parts
                   WHERE pc_id=? ORDER BY position""",
                (pc_id,),
            ).fetchall()
        self.assertEqual(
            [(row["item_id"], row["position"]) for row in memberships],
            [(first_ram, 0), (added_ram, 1), (removed_ram, 2)],
        )

        with self.assertRaisesRegex(ValidationError, "Other PC"):
            self.services.update_pc(pc_id, "Invalid", [first_ram, other_part])
        self.assertEqual(
            tuple(
                part.id
                for part in next(
                    pc for pc in self.services.list_pcs() if pc.id == pc_id
                ).parts
            ),
            (first_ram, added_ram, removed_ram),
        )
        self.assertEqual(
            next(pc for pc in self.services.list_pcs() if pc.id == other_pc_id)
            .parts[0]
            .id,
            other_part,
        )

    def test_sold_pc_keeps_its_case_insensitive_name_reservation(self):
        original_id = self.buy("Original", "CPU", 100)
        spare_id = self.buy("Spare", "RAM", 50)
        pc_id = self.services.assemble_pc("Gaming PC", [original_id])

        with self.assertRaisesRegex(ValidationError, "already exists"):
            self.services.assemble_pc(" gaming pc ", [spare_id])

        sale_id = self.services.sell_pc(pc_id, SaleTerms.create(120))
        with self.assertRaisesRegex(ValidationError, "already exists"):
            self.services.assemble_pc("GAMING PC", [spare_id])
        self.services.undo_sale(sale_id)
        self.assertEqual([pc.name for pc in self.services.list_pcs()], ["Gaming PC"])
        self.assertEqual(self.services.list_sales(), ())

    def test_pc_undo_restores_same_identity_without_reconstruction(self):
        old_id = self.buy("Old CPU", "CPU", 100)
        pc_id = self.services.assemble_pc("PC 1", [old_id])
        sale_id = self.services.sell_pc(pc_id, SaleTerms.create(125))
        self.services.undo_sale(sale_id)

        self.assertEqual(self.services.list_sales(), ())
        self.assertEqual(len(self.services.list_pcs()), 1)
        self.assertEqual(self.services.list_pcs()[0].id, pc_id)

    def test_sale_date_before_any_purchase_is_rejected_atomically(self):
        tomorrow = TEST_DATE + timedelta(days=1)
        ids = [self.buy("CPU", "CPU", 100, tomorrow), self.buy("RAM", "RAM", 50)]

        with self.assertRaisesRegex(ValidationError, "purchase date"):
            self.services.sell_items(ids, SaleTerms.create(200, TEST_DATE))

        self.assertEqual(self.services.list_sales(), ())
        self.assertEqual({item.id for item in self.services.list_inventory()}, set(ids))

    def test_component_edit_and_group_delete_are_atomic(self):
        ids = [self.buy("Cable", "Extra", 5), self.buy("Cable", "Extra", 6)]
        self.services.update_expense(
            ids[0], NewExpense.create("Power Cable", "Extra", 7, TEST_DATE)
        )
        self.assertEqual(self.services.list_expenses()[0].name, "Power Cable")

        self.services.assemble_pc("PC 1", [ids[1]])
        with self.assertRaises(ValidationError):
            self.services.delete_expenses(ids)
        self.assertEqual({item.id for item in self.services.list_expenses()}, set(ids))

    def test_bulk_commands_stay_below_sqlite_parameter_limits(self):
        ids = [self.buy(f"Bulk {index}", "Extra", 1) for index in range(6)]
        original_connect = Database.connect

        def limited_connect(database, *args, **kwargs):
            connection = original_connect(database, *args, **kwargs)
            connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 4)
            return connection

        with (
            patch.object(Database, "connect", new=limited_connect),
            patch("pcims.db.command_support.SQLITE_ID_BATCH_SIZE", 2),
        ):
            pc_id = self.services.assemble_pc("Batched PC", ids)
            self.services.disassemble_pc(pc_id)
            sale_id = self.services.sell_items(ids, SaleTerms.create(12))
            self.services.undo_sale(sale_id)
            self.services.delete_expenses(ids)

        self.assertEqual(self.services.list_expenses(), ())

    def test_updating_pc_name_does_not_rewrite_parts(self):
        item_id = self.buy("CPU", "CPU", 100)
        pc_id = self.services.assemble_pc("PC 1", [item_id])
        self.services.update_pc(pc_id, "Workstation", [item_id])

        pc = self.services.list_pcs()[0]
        self.assertEqual(pc.name, "Workstation")
        self.assertEqual(pc.parts[0].name, "CPU")

    def test_financial_summary_uses_current_inventory_and_realized_sales(self):
        sold_id = self.buy("Sold", "Extra", 25)
        self.buy("Stock", "Extra", 40)
        self.services.sell_items([sold_id], SaleTerms.create(50))

        summary = self.services.financial_summary()
        self.assertEqual(summary.expense_cents, 6500)
        self.assertEqual(summary.income_cents, 5000)
        self.assertEqual(summary.profit_cents, 2500)
        self.assertEqual(summary.realized_cost_cents, 2500)
        self.assertEqual(summary.roi_basis_points, 10_000)
        self.assertEqual(summary.inventory_cents, 4000)
        self.assertEqual(summary.cash_flow_cents, -1500)

    def test_balance_dashboard_aggregates_selected_period_and_fills_time_buckets(
        self,
    ):
        old_item = self.buy("Old CPU", "CPU", 100, date(2026, 1, 10))
        self.buy("August RAM", "RAM", 50, date(2026, 8, 14))
        second_sale_item = self.buy("August cable", "Extra", 10, date(2026, 8, 15))
        self.services.sell_items([old_item], SaleTerms.create(160, date(2026, 8, 20)))
        self.services.sell_items(
            [second_sale_item], SaleTerms.create(15, date(2026, 8, 21))
        )

        snapshot = self.services.balance_snapshot(date(2026, 8, 1), date(2026, 8, 31))

        self.assertEqual(snapshot.bucket, "day")
        self.assertEqual(len(snapshot.points), 31)
        self.assertEqual(snapshot.summary.purchase_cents, 6_000)
        self.assertEqual(snapshot.summary.revenue_cents, 17_500)
        self.assertEqual(snapshot.summary.realized_cost_cents, 11_000)
        self.assertEqual(snapshot.summary.profit_cents, 6_500)
        self.assertEqual(snapshot.summary.cash_flow_cents, 11_500)
        self.assertEqual(snapshot.summary.roi_basis_points, 5_909)
        self.assertEqual(snapshot.summary.profit_margin_basis_points, 3_714)
        self.assertEqual(snapshot.summary.average_sale_cents, 8_750)
        self.assertEqual(snapshot.summary.purchase_count, 2)
        self.assertEqual(snapshot.summary.sale_count, 2)
        self.assertEqual(snapshot.summary.sold_item_count, 2)
        self.assertEqual(snapshot.summary.current_inventory_cents, 5_000)
        august_twentieth = next(
            point
            for point in snapshot.points
            if point.period_start == date(2026, 8, 20)
        )
        self.assertEqual(august_twentieth.revenue_cents, 16_000)
        self.assertEqual(august_twentieth.profit_cents, 6_000)
        self.assertEqual(august_twentieth.sale_count, 1)

        all_time = self.services.balance_snapshot(None, date(2026, 8, 31))
        self.assertEqual(all_time.start_date, date(2026, 1, 10))
        self.assertEqual(all_time.end_date, date(2026, 8, 31))
        self.assertEqual(all_time.bucket, "month")
        self.assertEqual(len(all_time.points), 8)
        self.assertEqual(all_time.summary.purchase_cents, 16_000)

    def test_balance_dashboard_validates_dates_and_handles_an_empty_period(self):
        with self.assertRaisesRegex(ValueError, "cannot be after"):
            self.services.balance_snapshot(date(2026, 8, 2), date(2026, 8, 1))
        with self.assertRaisesRegex(TypeError, "end date"):
            self.services.balance_snapshot(date(2026, 8, 1), "2026-08-02")  # type: ignore[arg-type]

        snapshot = self.services.balance_snapshot(date(2026, 8, 1), date(2026, 8, 3))
        self.assertEqual(len(snapshot.points), 3)
        self.assertTrue(
            all(
                point.purchase_cents == point.revenue_cents == point.profit_cents == 0
                for point in snapshot.points
            )
        )
        self.assertEqual(snapshot.summary.current_inventory_cents, 0)

    def test_balance_dashboard_explicit_period_uses_one_aggregate_round_trip(self):
        statements: list[str] = []
        original_connect = Database.connect

        def traced_connect(database, *args, **kwargs):
            connection = original_connect(database, *args, **kwargs)
            connection.set_trace_callback(statements.append)
            return connection

        with patch.object(Database, "connect", new=traced_connect):
            self.services.balance_snapshot(date(2026, 8, 1), date(2026, 8, 31))

        reads = [
            statement
            for statement in statements
            if statement.lstrip().upper().startswith(("SELECT", "WITH"))
        ]
        self.assertEqual(len(reads), 1)
        self.assertTrue(reads[0].lstrip().upper().startswith("WITH SALE_TOTALS"))

    def test_verified_backup_restore_and_retention(self):
        self.buy("Old state", "CPU", 10)
        backup_directory = Path(self.temporary_directory.name) / "backups"
        source = create_backup(backup_directory, database=self.database)
        self.buy("New state", "RAM", 20)
        for _ in range(13):
            create_backup(backup_directory, database=self.database)

        result = restore_backup(source, database=self.database)

        self.assertEqual(
            [item.name for item in self.services.list_expenses()], ["Old state"]
        )
        self.assertEqual(result.source_path, source.path)
        validate_database(result.safety_backup)
        self.assertTrue(source.path.is_file())
        self.assertLessEqual(len(list(backup_directory.glob("pcims_*.db"))), 15)

    def test_invalid_backup_retention_is_rejected_before_writing(self):
        backup_directory = Path(self.temporary_directory.name) / "invalid-retention"
        for keep in (0, -1, True, 1.5, "2"):
            with (
                self.subTest(keep=keep),
                self.assertRaisesRegex(ValueError, "positive integer"),
            ):
                create_backup(backup_directory, keep=keep, database=self.database)
        self.assertFalse(backup_directory.exists())

    def test_restore_retention_never_prunes_the_selected_source(self):
        self.buy("Protected source", "CPU", 10)
        source = create_backup(keep=20, database=self.database)
        for _ in range(14):
            create_backup(keep=20, database=self.database)

        result = restore_backup(source, database=self.database)

        self.assertTrue(source.path.is_file())
        self.assertEqual(result.source_path, source.path)

    def test_backup_and_restore_support_uri_special_characters_in_paths(self):
        special_directory = Path(self.temporary_directory.name) / "data # 100% ready"
        special_database = special_directory / "inventory #1%.db"
        database = Database.at(special_database)
        services = ApplicationServices(database)
        services.initialize()
        old_id = services.add_expenses(
            [NewExpense.create("Old state", "CPU", 10, TEST_DATE)]
        )[0]
        backup = create_backup(special_directory / "backups #1%", database=database)
        services.add_expenses([NewExpense.create("New state", "RAM", 20, TEST_DATE)])

        restore_backup(backup, database=database)

        self.assertEqual(
            [item.name for item in services.list_expenses()], ["Old state"]
        )
        self.assertEqual(services.list_expenses()[0].id, old_id)
        validate_database(backup)

    def test_backup_retention_failure_does_not_hide_verified_backup(self):
        self.buy("Keep", "CPU", 10)
        backup_directory = Path(self.temporary_directory.name) / "backups"
        first = create_backup(backup_directory, keep=1, database=self.database)
        self.buy("Changed", "RAM", 1)
        original_unlink = Path.unlink

        def fail_for_oldest(path, *args, **kwargs):
            if path == first.path:
                raise PermissionError("simulated locked backup")
            return original_unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", new=fail_for_oldest):
            result = create_backup(backup_directory, keep=1, database=self.database)

        self.assertTrue(result.path.is_file())
        validate_database(result)
        self.assertTrue(result.has_warnings)
        self.assertIn("simulated locked backup", result.warning_text)

    def test_backup_scan_failure_does_not_hide_verified_backup(self):
        self.buy("Keep", "CPU", 10)
        backup_directory = Path(self.temporary_directory.name) / "backups"

        with patch.object(
            Path, "glob", side_effect=OSError("simulated directory scan failure")
        ):
            result = create_backup(backup_directory, database=self.database)

        self.assertTrue(result.path.is_file())
        validate_database(result)
        self.assertTrue(result.has_warnings)
        self.assertIn("simulated directory scan failure", result.warning_text)

    def test_backup_retention_never_deletes_another_database_generation(self):
        shared_directory = Path(self.temporary_directory.name) / "shared-backups"
        first = create_backup(shared_directory, keep=1, database=self.database)
        other_database = Database.at(
            Path(self.temporary_directory.name) / "other-inventory.db"
        )
        initialize_database(other_database)
        other_first = create_backup(shared_directory, keep=1, database=other_database)

        self.buy("Changed", "RAM", 1)
        create_backup(shared_directory, keep=1, database=self.database)

        self.assertFalse(first.path.exists())
        self.assertTrue(other_first.path.exists())
        self.assertNotEqual(
            first.path.name.split("_")[1], other_first.path.name.split("_")[1]
        )

    def test_clock_rollback_cannot_delete_the_just_created_backup(self):
        first = create_backup(keep=2, database=self.database)
        os.utime(first.path, ns=(1, 1))
        old_clock = datetime(2000, 1, 1, tzinfo=UTC)
        self.buy("Changed", "RAM", 1)

        with patch("pcims.db.backup.datetime") as clock:
            clock.now.return_value = old_clock
            second = create_backup(keep=1, database=self.database)

        self.assertFalse(first.path.exists())
        self.assertTrue(second.path.exists())

    def test_unchanged_database_reuses_verified_backup(self):
        repeated_time = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
        with patch("pcims.db.backup.datetime") as clock:
            clock.now.return_value = repeated_time
            first = create_backup(database=self.database)
            second = create_backup(database=self.database)

        self.assertEqual(first.path, second.path)
        self.assertTrue(first.path.is_file())
        self.assertTrue(second.reused)
        self.assertEqual(len(tuple(first.path.parent.glob("pcims_*.db"))), 1)

    def test_non_file_cannot_consume_a_backup_retention_slot(self):
        first = create_backup(keep=2, database=self.database)
        matching_directory = first.path.with_name(
            f"{first.path.name[:19]}9999-12-31_23-59-59_999999.db"
        )
        matching_directory.mkdir()
        future = (datetime.now(UTC) + timedelta(days=1)).timestamp()
        os.utime(matching_directory, (future, future))

        self.buy("Changed", "RAM", 1)
        second = create_backup(keep=1, database=self.database)

        self.assertFalse(first.path.exists())
        self.assertTrue(second.path.exists())
        self.assertTrue(matching_directory.is_dir())

    def test_backup_flushes_file_and_directory_around_atomic_publish(self):
        self.buy("Keep", "CPU", 10)
        backup_directory = Path(self.temporary_directory.name) / "durable-backups"
        events: list[tuple[str, Path]] = []
        real_replace = os.replace

        def observed_replace(source, destination):
            events.append(("replace", Path(destination)))
            real_replace(source, destination)

        with (
            patch(
                "pcims.db.backup._sync_file",
                side_effect=lambda path: events.append(("file", path)),
            ),
            patch(
                "pcims.db.backup._sync_directory",
                side_effect=lambda path: events.append(("directory", path)),
            ),
            patch("pcims.db.backup.os.replace", side_effect=observed_replace),
        ):
            result = create_backup(backup_directory, database=self.database)

        self.assertEqual(
            [event for event, _path in events], ["file", "replace", "directory"]
        )
        self.assertEqual(events[-1][1], backup_directory.resolve())
        validate_database(result)

    def test_post_publish_directory_failure_reports_completed_backup_warning(self):
        self.buy("Durable enough", "CPU", 10)
        with patch(
            "pcims.db.backup._sync_directory",
            side_effect=OSError("simulated directory flush failure"),
        ):
            result = create_backup(database=self.database)

        self.assertTrue(result.path.is_file())
        self.assertTrue(result.has_warnings)
        self.assertFalse(result.durable)
        self.assertIn("Backup was created", result.warning_text)
        validate_database(result)

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
            create_backup(backup_directory, database=self.database)

        notes = getattr(raised.exception, "__notes__", ())
        self.assertTrue(any("temporary file locked" in note for note in notes))

    def test_restore_rejects_old_or_corrupt_databases_without_changes(self):
        self.buy("Keep me", "CPU", 10)
        invalid = Path(self.temporary_directory.name) / "invalid.db"
        invalid.write_text("not sqlite", encoding="utf-8")

        with self.assertRaises(sqlite3.DatabaseError):
            restore_backup(invalid, database=self.database)
        self.assertEqual(
            [item.name for item in self.services.list_expenses()], ["Keep me"]
        )

    def test_restore_rejects_a_hard_link_alias_of_the_live_database(self):
        self.buy("Keep me", "CPU", 10)
        alias = Path(self.temporary_directory.name) / "live-alias.db"
        os.link(self.database_path, alias)

        with self.assertRaisesRegex(ValueError, "active database"):
            restore_backup(alias, database=self.database)

        self.assertEqual(
            [item.name for item in self.services.list_expenses()], ["Keep me"]
        )

    def test_restore_requires_a_distinct_durable_safety_backup(self):
        self.buy("Old state", "CPU", 10)
        source = create_backup(database=self.database)
        self.buy("Current state", "RAM", 20)
        unsafe_results = (
            BackupResult(
                self.database_path.with_name("not-durable.db"),
                ("directory flush failed",),
                False,
            ),
            BackupResult(source.path),
            BackupResult(self.database_path),
        )

        for unsafe in unsafe_results:
            with (
                self.subTest(unsafe=unsafe),
                patch("pcims.db.backup._create_backup", return_value=unsafe),
                self.assertRaises((OSError, RuntimeError)),
            ):
                restore_backup(source, database=self.database)

        self.assertEqual(
            [item.name for item in self.services.list_expenses()],
            ["Old state", "Current state"],
        )

    def test_restore_removes_stale_wal_sidecars_before_replacing_main_file(self):
        self.buy("Restored state", "CPU", 10)
        source = create_backup(database=self.database)
        sidecars = tuple(
            Path(f"{self.database_path}{suffix}") for suffix in ("-wal", "-shm")
        )
        for sidecar in sidecars:
            sidecar.write_bytes(b"stale journal")
        safety = BackupResult(self.database_path.with_name("safety.db"))

        with patch("pcims.db.backup._create_backup", return_value=safety):
            result = restore_backup(source, database=self.database)

        self.assertEqual(result.source_path, source.path)
        self.assertEqual(result.safety_backup, safety)
        self.assertTrue(all(not sidecar.exists() for sidecar in sidecars))
        self.assertEqual(
            [item.name for item in self.services.list_expenses()], ["Restored state"]
        )

    def test_failed_restore_replace_leaves_live_database_self_contained(self):
        self.buy("Keep current state", "CPU", 10)
        source_database = Database.at(
            Path(self.temporary_directory.name) / "replacement-source.db"
        )
        source_services = ApplicationServices(source_database)
        source_services.initialize()
        source_services.add_expenses(
            [NewExpense.create("Replacement state", "RAM", "20.00", "2024-01-01")]
        )
        source = source_services.create_backup()
        safety = BackupResult(self.database_path.with_name("safety.db"))
        real_replace = os.replace

        def fail_live_replace(source_path, destination_path):
            if Path(destination_path).resolve() == self.database.path:
                raise PermissionError("simulated live database lock")
            real_replace(source_path, destination_path)

        with (
            patch("pcims.db.backup._create_backup", return_value=safety),
            patch("pcims.db.backup.os.replace", side_effect=fail_live_replace),
            self.assertRaisesRegex(PermissionError, "simulated live database lock"),
        ):
            restore_backup(source, database=self.database)

        validate_database(self.database_path)
        self.assertEqual(
            [item.name for item in self.services.list_expenses()],
            ["Keep current state"],
        )

    def test_post_replace_directory_failure_reports_completed_restore_warning(self):
        self.buy("Old state", "CPU", 10)
        source = create_backup(database=self.database)
        self.buy("Discarded state", "RAM", 20)

        sync_calls = 0

        def fail_restored_directory(_path):
            nonlocal sync_calls
            sync_calls += 1
            if sync_calls == 2:
                raise OSError("simulated restore directory flush failure")

        with patch(
            "pcims.db.backup._sync_directory", side_effect=fail_restored_directory
        ):
            result = restore_backup(source, database=self.database)

        self.assertTrue(result.has_warnings)
        self.assertFalse(result.durable)
        self.assertTrue(result.safety_backup.durable)
        self.assertIn("Database was restored", result.warning_text)
        self.assertEqual(
            [item.name for item in self.services.list_expenses()], ["Old state"]
        )

    def test_backup_rejects_foreign_key_violations(self):
        item_id = self.buy("CPU", "CPU", 10)
        with closing(sqlite3.connect(self.database_path)) as database:
            database.execute(
                "INSERT INTO pc_parts (pc_id,item_id,position) VALUES (999,?,0)",
                (item_id,),
            )
            database.commit()

        with self.assertRaisesRegex(sqlite3.DatabaseError, "foreign-key"):
            create_backup(
                Path(self.temporary_directory.name) / "invalid-backups",
                database=self.database,
            )

    def test_laptop_value_transfer_is_not_counted_as_an_extra_purchase(self):
        proof = NewProof("receipt.pdf", "application/pdf", b"%PDF-1.7\nLaptop receipt")
        laptop_id = self.services.add_laptop(
            NewExpense.create("ThinkPad T480", "Extra", 500, TEST_DATE), (proof,)
        )
        ram_a = self.buy("8 GB RAM", "RAM", 10)
        self.buy("Spare RAM", "RAM", 15)
        ssd = self.buy("Replacement SSD", "SSD", 20)

        first_removed = self.services.extract_laptop_component(
            laptop_id,
            "RAM",
            1,
            NewExpense.create("Factory RAM A", "RAM", 30, TEST_DATE),
            ram_a,
        )
        self.services.extract_laptop_component(
            laptop_id,
            "RAM",
            2,
            NewExpense.create("Factory RAM B", "RAM", 20, TEST_DATE),
        )
        self.services.extract_laptop_component(
            laptop_id,
            "SSD",
            1,
            NewExpense.create("Factory SSD", "SSD", 50, TEST_DATE),
            ssd,
        )

        laptop = self.services.laptop_snapshot().laptops[0]
        self.assertEqual(laptop.original_cost_cents, 50_000)
        self.assertEqual(laptop.item.price_cents, 40_000)
        self.assertEqual(laptop.current_cost_cents, 43_000)
        self.assertEqual(
            [(slot.component_type, slot.slot_number) for slot in laptop.slots],
            [("RAM", 1), ("RAM", 2), ("SSD", 1)],
        )
        self.assertEqual(len(laptop.item.proofs), 1)

        summary = self.services.financial_summary()
        self.assertEqual(summary.expense_cents, 54_500)
        self.assertEqual(summary.inventory_cents, 54_500)
        dashboard = self.services.balance_snapshot(TEST_DATE, TEST_DATE).summary
        self.assertEqual(dashboard.purchase_cents, 54_500)
        self.assertEqual(dashboard.purchase_count, 4)
        self.assertNotIn(
            "Factory RAM A", {item.name for item in self.services.list_expenses()}
        )
        inventory_names = {item.name for item in self.services.list_inventory()}
        self.assertIn("Factory RAM A", inventory_names)
        self.assertIn("Spare RAM", inventory_names)
        self.assertNotIn("ThinkPad T480", inventory_names)
        self.assertNotIn("8 GB RAM", inventory_names)

        removed = next(
            item for item in self.services.list_inventory() if item.id == first_removed
        )
        self.services.update_expense(
            removed.id,
            NewExpense.create(removed.name, "RAM", 35, TEST_DATE),
        )
        adjusted = self.services.laptop_snapshot().laptops[0]
        self.assertEqual(adjusted.item.price_cents, 39_500)
        self.assertEqual(self.services.financial_summary().inventory_cents, 54_500)

        with self.assertRaisesRegex(ValidationError, "not available for sale"):
            self.services.sell_items([laptop_id], SaleTerms.create(600, TEST_DATE))
        with self.assertRaisesRegex(ValidationError, "Restore all"):
            self.services.delete_laptop(laptop_id)

    def test_laptop_sale_undo_replacement_and_factory_restore_are_reversible(self):
        laptop_id = self.services.add_laptop(
            NewExpense.create("Latitude 7490", "Extra", 300, TEST_DATE)
        )
        replacement_id = self.buy("Replacement RAM", "RAM", 25)
        extracted_id = self.services.extract_laptop_component(
            laptop_id,
            "RAM",
            1,
            NewExpense.create("Factory RAM", "RAM", 40, TEST_DATE),
            replacement_id,
        )

        sale_id = self.services.sell_laptop(laptop_id, SaleTerms.create(400, TEST_DATE))
        sale = self.services.list_sales()[0]
        self.assertEqual(sale.kind, "laptop")
        self.assertEqual({item.id for item in sale.items}, {laptop_id, replacement_id})
        self.assertEqual(sale.cost_cents, 28_500)
        self.assertEqual(self.services.financial_summary().inventory_cents, 4_000)
        extracted = next(
            item for item in self.services.list_inventory() if item.id == extracted_id
        )
        self.services.update_expense(
            extracted_id,
            NewExpense.create(extracted.name, "RAM", 45, TEST_DATE),
        )
        self.assertEqual(self.services.list_sales()[0].cost_cents, 28_000)
        self.services.update_laptop(
            laptop_id,
            NewExpense.create("Latitude 7490 corrected", "Extra", 310, TEST_DATE),
        )
        corrected_sale = self.services.list_sales()[0]
        self.assertEqual(corrected_sale.name, "Latitude 7490 corrected")
        self.assertEqual(corrected_sale.cost_cents, 29_000)
        with self.assertRaisesRegex(ValidationError, "cannot be after"):
            self.services.update_laptop(
                laptop_id,
                NewExpense.create(
                    "Too late", "Extra", 310, TEST_DATE + timedelta(days=1)
                ),
            )
        with self.assertRaisesRegex(ValidationError, "Undo the laptop sale"):
            self.services.restore_laptop_component(laptop_id, "RAM", 1)

        self.services.undo_sale(sale_id)
        self.services.set_laptop_replacement(laptop_id, "RAM", 1, None)
        self.services.restore_laptop_component(laptop_id, "RAM", 1)
        restored = self.services.laptop_snapshot().laptops[0]
        self.assertEqual(restored.item.price_cents, 31_000)
        self.assertEqual(restored.slots, ())
        self.assertNotIn(
            extracted_id, {item.id for item in self.services.list_inventory()}
        )
        self.assertIn(
            replacement_id, {item.id for item in self.services.list_inventory()}
        )
        self.services.delete_laptop(laptop_id)
        self.assertEqual(self.services.laptop_snapshot().laptops, ())


if __name__ == "__main__":
    unittest.main()
