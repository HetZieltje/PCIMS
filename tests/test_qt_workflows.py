import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from datetime import date, timedelta
from importlib.metadata import version
from pathlib import Path
from unittest.mock import MagicMock, call, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import (
    QDate,
    QEventLoop,
    QLockFile,
    QSettings,
    Qt,
    QThreadPool,
    QTimer,
)
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QMessageBox, QTableView, QWidget
from shiboken6 import delete as delete_qt_object

from pcims.app.application import acquire_instance_lock, create_application, main
from pcims.app.assembly_model import AssemblyTreeModel
from pcims.app.dialogs import ExpenseEditDialog, PCEditDialog
from pcims.app.errors import install_exception_hook, log_exception
from pcims.app.main_window import MainWindow
from pcims.app.pages.assemble import AssemblePage
from pcims.app.pages.inventory import InventoryPage
from pcims.app.pages.purchases import PurchasesPage, StagedPurchase
from pcims.app.pages.sales import SalesPage
from pcims.app.table_model import (
    Column,
    RecordTableModel,
    configure_table_view,
    selected_ids,
)
from pcims.app.tasks import TaskManager
from pcims.contracts import BackupResult
from pcims.db.connection import Database
from pcims.db.errors import ValidationError
from pcims.domain import NewExpense, SaleTerms
from pcims.services import ApplicationServices

TEST_DATE = date(2026, 8, 14)


class QtWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings_directory = tempfile.TemporaryDirectory()
        cls.data_environment = patch.dict(
            os.environ, {"PCIMS_DATA_DIR": cls.settings_directory.name}
        )
        cls.data_environment.start()
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(
            QSettings.Format.IniFormat,
            QSettings.Scope.UserScope,
            cls.settings_directory.name,
        )
        cls.application = create_application([])

    @classmethod
    def tearDownClass(cls):
        cls.application.processEvents()
        cls.data_environment.stop()
        cls.settings_directory.cleanup()

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        settings = QSettings("PCIMS", "PCIMS")
        settings.clear()
        settings.sync()
        self.database = Database.at(Path(self.temporary_directory.name) / "qt-test.db")
        self.services = ApplicationServices(self.database)
        self.services.initialize()
        self.tasks = TaskManager()

    def tearDown(self):
        self.wait_until(lambda: not self.tasks.active)
        self.application.processEvents()
        self.temporary_directory.cleanup()

    def wait_until(self, predicate, timeout=3.0):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            self.application.processEvents()
            time.sleep(0.005)
        self.assertTrue(predicate(), "Timed out waiting for an asynchronous Qt task")

    def wait_for_window(self, window):
        self.wait_until(lambda: not window.refresh_running)

    def wait_for_page(self, page):
        self.wait_until(lambda: not page.command_running)

    def purchase(self, name, item_type, price):
        return self.services.add_expenses(
            [NewExpense.create(name, item_type, price, TEST_DATE)]
        )[0]

    @staticmethod
    def check_all_assembly_parts(page):
        checked = []
        model = page.tree_model
        for group_row in range(model.rowCount()):
            group = model.index(group_row, 0)
            for child_row in range(model.rowCount(group)):
                child = model.index(child_row, 0, group)
                model.setData(
                    child,
                    Qt.CheckState.Checked,
                    Qt.ItemDataRole.CheckStateRole,
                )
                checked.append(child.data(Qt.ItemDataRole.UserRole))
        return checked

    def test_main_window_constructs_and_refreshes_every_page(self):
        window = MainWindow(self.services)
        window.show()
        self.wait_for_window(window)

        self.assertEqual(window.tabs.count(), 5)
        self.assertGreaterEqual(window.width(), 900)
        self.assertTrue(all(page.tasks is window.tasks for page in window.pages))
        for index in range(window.tabs.count()):
            window.tabs.setCurrentIndex(index)
            window.refresh_current(index)
            self.wait_for_window(window)
        window.apply_theme("dark")
        window.apply_theme("light")
        window.refresh_all()
        self.wait_for_window(window)
        window.deleteLater()

    def test_qt_application_version_matches_installed_distribution(self):
        self.assertEqual(self.application.applicationVersion(), version("pcims"))

    def test_page_construction_performs_no_database_io(self):
        services = MagicMock(spec=ApplicationServices)
        pages = (
            InventoryPage(services, tasks=self.tasks),
            PurchasesPage(services, tasks=self.tasks),
            AssemblePage(services, tasks=self.tasks),
            SalesPage(services, tasks=self.tasks),
        )

        self.assertEqual(services.mock_calls, [])
        for page in pages:
            page.deleteLater()

    def test_data_changes_refresh_only_visible_page_until_tab_is_opened(self):
        window = MainWindow(self.services)
        self.wait_for_window(window)
        window.tabs.setCurrentWidget(window.inventory_page)
        with (
            patch.object(
                window.inventory_page,
                "load_snapshot",
                wraps=window.inventory_page.load_snapshot,
            ) as inventory_refresh,
            patch.object(
                window.purchases_page,
                "load_snapshot",
                wraps=window.purchases_page.load_snapshot,
            ) as purchases_refresh,
            patch.object(
                window.assemble_page,
                "load_snapshot",
                wraps=window.assemble_page.load_snapshot,
            ) as assemble_refresh,
            patch.object(
                window.sales_page,
                "load_snapshot",
                wraps=window.sales_page.load_snapshot,
            ) as sales_refresh,
        ):
            window.inventory_page.data_changed.emit()
            self.wait_for_window(window)
            inventory_refresh.assert_called_once()
            purchases_refresh.assert_not_called()
            assemble_refresh.assert_not_called()
            sales_refresh.assert_not_called()

            window.tabs.setCurrentWidget(window.purchases_page)
            self.wait_for_window(window)
            purchases_refresh.assert_called_once()
            assemble_refresh.assert_not_called()
            sales_refresh.assert_not_called()
        window.deleteLater()

    def test_inventory_filters_use_loaded_data_without_database_queries(self):
        self.purchase("Case fan", "Fan", 10)
        page = InventoryPage(self.services, tasks=self.tasks)
        page.refresh()
        with (
            patch(
                "pcims.services.ApplicationServices.list_inventory"
            ) as inventory_query,
            patch("pcims.services.ApplicationServices.list_pcs") as pc_query,
        ):
            page.search.setText("fan")
            page.type_filter.setCurrentText("Fan")
            page.status_filter.setCurrentText("Available only")
        inventory_query.assert_not_called()
        pc_query.assert_not_called()
        self.assertEqual(page.parts_model.rowCount(), 1)
        page.deleteLater()

    def test_main_window_restores_geometry_tab_and_splitters(self):
        first = MainWindow(self.services)
        self.wait_for_window(first)
        first.resize(1080, 720)
        first.tabs.setCurrentIndex(3)
        first.inventory_page.splitter.setSizes((800, 200))
        first.sales_page.splitter.setSizes((300, 700))
        first.sales_page.detail_splitter.setSizes((500, 100))
        first.show()
        self.application.processEvents()
        expected_geometry = first.saveGeometry()
        expected_splitters = (
            first.inventory_page.splitter.saveState(),
            first.sales_page.splitter.saveState(),
            first.sales_page.detail_splitter.saveState(),
        )
        expected_ratios = tuple(
            tuple(size / sum(splitter.sizes()) for size in splitter.sizes())
            for splitter in (
                first.inventory_page.splitter,
                first.sales_page.splitter,
                first.sales_page.detail_splitter,
            )
        )
        first._save_window_state()
        settings = QSettings("PCIMS", "PCIMS")
        self.assertEqual(settings.value("window/geometry"), expected_geometry)
        for key, state in zip(
            ("inventory", "sales", "sales_details"), expected_splitters
        ):
            self.assertEqual(settings.value(f"window/splitters/{key}"), state)
        first.deleteLater()

        with patch.object(MainWindow, "restoreGeometry", return_value=True) as restore:
            second = MainWindow(self.services)
            self.wait_for_window(second)
        restore.assert_called_once_with(expected_geometry)
        second.show()
        self.application.processEvents()
        self.assertEqual(second.tabs.currentIndex(), 3)
        for splitter, expected in zip(
            (
                second.inventory_page.splitter,
                second.sales_page.splitter,
                second.sales_page.detail_splitter,
            ),
            expected_ratios,
        ):
            actual = tuple(size / sum(splitter.sizes()) for size in splitter.sizes())
            for actual_ratio, expected_ratio in zip(actual, expected):
                self.assertAlmostEqual(actual_ratio, expected_ratio, delta=0.03)
        second.deleteLater()

    def test_purchase_page_allocates_quantity_total_and_commits(self):
        page = PurchasesPage(self.services, tasks=self.tasks)
        page.refresh()
        page.name.setText("Case fan")
        page.type.setCurrentText("Fan")
        page.quantity.setValue(3)
        page.price.setText("10,00")
        page.total_for_quantity.setChecked(True)
        page.add_line()

        self.assertEqual(len(page._staged), 3)
        self.assertEqual(
            [item.expense.price_cents for item in page._staged], [334, 333, 333]
        )
        with patch("pcims.app.pages.purchases.QMessageBox.information"):
            page.commit_purchase()
            self.wait_for_page(page)

        self.assertEqual(
            [item.price_cents for item in self.services.list_expenses()],
            [334, 333, 333],
        )
        page.deleteLater()

    def test_purchase_page_reports_database_failure_without_losing_staged_work(self):
        page = PurchasesPage(self.services, tasks=self.tasks)
        page.refresh()
        page.name.setText("Case fan")
        page.type.setCurrentText("Fan")
        page.price.setText("10.00")
        page.add_line()

        with (
            patch(
                "pcims.services.ApplicationServices.add_expenses",
                side_effect=sqlite3.OperationalError("simulated disk failure"),
            ),
            patch("pcims.app.async_page.show_error") as show_error,
            patch("pcims.app.tasks.log_exception") as log_error,
        ):
            page.commit_purchase()
            self.wait_for_page(page)

        self.assertTrue(page.has_staged_items)
        show_error.assert_called_once()
        log_error.assert_called_once()
        self.assertIn("simulated disk failure", str(show_error.call_args.args[2]))
        page.deleteLater()

    def test_expected_domain_conflict_is_reported_without_crash_traceback(self):
        page = PurchasesPage(self.services, tasks=self.tasks)
        page.name.setText("Conflicting item")
        page.price.setText("1.00")
        page.add_line()

        with (
            patch(
                "pcims.services.ApplicationServices.add_expenses",
                side_effect=ValidationError("expected conflict"),
            ),
            patch("pcims.app.async_page.show_error") as show_error,
            patch("pcims.app.tasks.log_exception") as log_error,
        ):
            page.commit_purchase()
            self.wait_for_page(page)

        show_error.assert_called_once()
        log_error.assert_not_called()
        page.deleteLater()

    def test_close_warns_before_discarding_staged_purchase(self):
        window = MainWindow(self.services)
        self.wait_for_window(window)
        window.purchases_page._staged.append(
            StagedPurchase(1, NewExpense.create("Pending", "Extra", 1, TEST_DATE))
        )
        event = QCloseEvent()
        with patch(
            "pcims.app.main_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ):
            window.closeEvent(event)
        self.assertFalse(event.isAccepted())
        window.deleteLater()

    def test_close_accepts_verified_backup_with_retention_warning(self):
        window = MainWindow(self.services)
        self.wait_for_window(window)
        window.show()
        event = QCloseEvent()
        loop = QEventLoop()
        outcome = BackupResult(
            Path(self.temporary_directory.name) / "verified.db",
            ("old.db: simulated lock",),
        )
        with (
            patch(
                "pcims.services.ApplicationServices.create_backup",
                return_value=outcome,
            ),
            patch(
                "pcims.app.main_window.QMessageBox.warning",
                side_effect=lambda *_: loop.quit(),
            ) as warning,
            patch("pcims.app.main_window.QMessageBox.question") as question,
        ):
            window.closeEvent(event)
            self.assertFalse(event.isAccepted())
            self.assertFalse(window.isEnabled())
            QTimer.singleShot(3000, loop.quit)
            loop.exec()

        self.assertTrue(window._closing_after_backup)
        self.assertTrue(window.isEnabled())
        warning.assert_called_once()
        question.assert_not_called()
        window.deleteLater()

    def test_failed_close_backup_returns_control_when_close_is_declined(self):
        window = MainWindow(self.services)
        self.wait_for_window(window)
        window.show()
        event = QCloseEvent()
        loop = QEventLoop()
        with (
            patch(
                "pcims.services.ApplicationServices.create_backup",
                side_effect=OSError("simulated disk failure"),
            ),
            patch(
                "pcims.app.main_window.QMessageBox.question",
                side_effect=lambda *_: (loop.quit(), QMessageBox.StandardButton.No)[1],
            ) as question,
        ):
            window.closeEvent(event)
            QTimer.singleShot(3000, loop.quit)
            loop.exec()

        self.assertFalse(window._closing_after_backup)
        self.assertFalse(window._close_backup_running)
        self.assertTrue(window.refreshes.accepting)
        self.assertTrue(window.isEnabled())
        self.assertTrue(window.isVisible())
        question.assert_called_once()
        window.deleteLater()

    def test_close_drains_inflight_refresh_before_destroying_window(self):
        window = MainWindow(self.services)
        self.wait_for_window(window)
        refresh_started = threading.Event()
        release_refresh = threading.Event()
        outcome = BackupResult(Path(self.temporary_directory.name) / "verified.db")

        def slow_snapshot():
            refresh_started.set()
            release_refresh.wait(2)
            return "late snapshot"

        window.refreshes.mark_dirty(window.inventory_page)
        with (
            patch.object(
                window.inventory_page,
                "load_snapshot",
                side_effect=slow_snapshot,
            ),
            patch.object(
                window.inventory_page,
                "apply_snapshot",
            ) as apply_snapshot,
            patch(
                "pcims.services.ApplicationServices.create_backup",
                return_value=outcome,
            ) as create_backup,
            patch.object(window, "close") as close,
        ):
            window.refresh_current()
            self.wait_until(refresh_started.is_set)
            event = QCloseEvent()
            window.closeEvent(event)
            self.application.processEvents()
            create_backup.assert_not_called()
            self.assertFalse(close.called)
            release_refresh.set()
            self.wait_until(lambda: close.called)

        apply_snapshot.assert_not_called()
        create_backup.assert_called_once()
        self.assertTrue(window._closing_after_backup)
        window.deleteLater()

    def test_close_backup_includes_an_inflight_committed_mutation(self):
        window = MainWindow(self.services)
        self.wait_for_window(window)
        page = window.purchases_page
        page.name.setText("Late GPU")
        page.type.setCurrentText("GPU")
        page.price.setText("100.00")
        page.add_line()
        mutation_started = threading.Event()
        release_mutation = threading.Event()
        backed_up_names = []
        outcome = BackupResult(Path(self.temporary_directory.name) / "verified.db")
        original_add = ApplicationServices.add_expenses

        def slow_add(services, items):
            mutation_started.set()
            release_mutation.wait(2)
            return original_add(services, items)

        def observed_backup(services, *_args, **_kwargs):
            backed_up_names.extend(item.name for item in services.list_expenses())
            return outcome

        with (
            patch.object(
                ApplicationServices,
                "add_expenses",
                autospec=True,
                side_effect=slow_add,
            ),
            patch.object(
                ApplicationServices,
                "create_backup",
                autospec=True,
                side_effect=observed_backup,
            ) as create_backup,
            patch.object(
                window.inventory_page,
                "load_snapshot",
                wraps=window.inventory_page.load_snapshot,
            ) as load_inventory,
            patch("pcims.app.pages.purchases.QMessageBox.information"),
            patch(
                "pcims.app.main_window.QMessageBox.question",
                return_value=QMessageBox.StandardButton.Yes,
            ),
            patch.object(window, "close") as close,
        ):
            page.commit_purchase()
            self.wait_until(mutation_started.is_set)
            window.closeEvent(QCloseEvent())
            self.application.processEvents()
            create_backup.assert_not_called()
            release_mutation.set()
            self.wait_until(lambda: close.called)

        self.assertEqual(backed_up_names, ["Late GPU"])
        load_inventory.assert_not_called()
        window.deleteLater()

    def test_restore_discards_staged_purchase_only_after_success(self):
        self.purchase("Backup item", "CPU", 100)
        backup = self.services.create_backup(
            Path(self.temporary_directory.name) / "backups"
        )
        self.purchase("Later item", "RAM", 50)
        window = MainWindow(self.services)
        self.wait_for_window(window)
        window.purchases_page._staged.append(
            StagedPurchase(1, NewExpense.create("Pending", "Extra", 1, TEST_DATE))
        )
        loop = QEventLoop()

        with (
            patch(
                "pcims.app.pages.settings.QFileDialog.getOpenFileName",
                return_value=(str(backup), "SQLite databases (*.db)"),
            ),
            patch(
                "pcims.app.pages.settings.ask_confirmation", return_value=True
            ) as confirm,
            patch(
                "pcims.app.pages.settings.QMessageBox.information",
                side_effect=lambda *_: loop.quit(),
            ),
        ):
            window.settings_page.restore_backup()
            self.assertFalse(window.isEnabled())
            self.assertTrue(window.purchases_page.has_staged_items)
            QTimer.singleShot(3000, loop.quit)
            loop.exec()
            self.wait_for_window(window)

        self.assertIn("Unrecorded purchase lines", confirm.call_args.args[2])
        self.assertTrue(window.isEnabled())
        self.assertFalse(window.purchases_page.has_staged_items)
        self.assertEqual(
            [item.name for item in self.services.list_expenses()], ["Backup item"]
        )
        window.deleteLater()

    def test_failed_async_restore_reenables_window_and_preserves_work(self):
        self.purchase("Existing item", "Extra", 10)
        window = MainWindow(self.services)
        self.wait_for_window(window)
        window.purchases_page._staged.append(
            StagedPurchase(1, NewExpense.create("Pending", "Extra", 1, TEST_DATE))
        )
        missing = Path(self.temporary_directory.name) / "missing.db"
        loop = QEventLoop()
        with (
            patch(
                "pcims.app.pages.settings.QFileDialog.getOpenFileName",
                return_value=(str(missing), "SQLite databases (*.db)"),
            ),
            patch("pcims.app.pages.settings.ask_confirmation", return_value=True),
            patch(
                "pcims.app.pages.settings.show_error",
                side_effect=lambda *_: loop.quit(),
            ) as show_error,
        ):
            window.settings_page.restore_backup()
            self.assertFalse(window.isEnabled())
            QTimer.singleShot(3000, loop.quit)
            loop.exec()

        self.assertTrue(window.isEnabled())
        self.assertTrue(window.purchases_page.has_staged_items)
        self.assertEqual(
            [item.name for item in self.services.list_expenses()], ["Existing item"]
        )
        self.assertEqual(window.settings_page.restore_button.text(), "Restore backup…")
        show_error.assert_called_once()
        window.deleteLater()

    def test_table_model_sorts_by_typed_values(self):
        model = RecordTableModel[tuple[int, str]](
            (Column("Price", lambda item: item[1], lambda item: item[0]),),
            lambda item: item[0],
        )
        model.set_records(((10000, "€100.00"), (900, "€9.00"), (2000, "€20.00")))
        model.sort(0, Qt.SortOrder.AscendingOrder)
        self.assertEqual(
            [model.index(row, 0).data() for row in range(3)],
            ["€9.00", "€20.00", "€100.00"],
        )

    def test_selected_ids_come_from_records_after_visual_sorting(self):
        model = RecordTableModel[tuple[int, str]](
            (
                Column("ID", lambda item: str(item[0]), lambda item: item[0]),
                Column("Name", lambda item: item[1], lambda item: item[1].casefold()),
            ),
            lambda item: item[0],
        )
        model.set_records(((1, "Zulu"), (2, "Alpha")))
        table = QTableView()
        configure_table_view(table, model)
        table.selectRow(0)
        table.sortByColumn(1, Qt.SortOrder.AscendingOrder)

        self.assertEqual(selected_ids(table), [1])
        table.selectAll()

        self.assertEqual(selected_ids(table), [2, 1])
        table.deleteLater()

    def test_table_model_resets_and_sorts_large_record_sets(self):
        model = RecordTableModel[tuple[int, str]](
            (
                Column("ID", lambda item: str(item[0]), lambda item: item[0]),
                Column("Name", lambda item: item[1], lambda item: item[1]),
            ),
            lambda item: item[0],
        )
        model.sort(0, Qt.SortOrder.AscendingOrder)
        model.set_records(
            tuple((number, f"Item {number}") for number in range(9999, -1, -1))
        )

        self.assertEqual(model.rowCount(), 10_000)
        self.assertEqual(model.index(0, 0).data(), "0")
        self.assertEqual(model.index(9999, 0).data(), "9999")

    def test_table_model_rejects_duplicate_record_identity_without_resetting(self):
        model = RecordTableModel[tuple[int, str]](
            (Column("Name", lambda item: item[1], lambda item: item[1]),),
            lambda item: item[0],
        )
        model.set_records(((1, "Existing"),))

        with self.assertRaisesRegex(ValueError, "unique"):
            model.set_records(((2, "First"), (2, "Second")))

        self.assertEqual(model.records, ((1, "Existing"),))

    def test_assembly_tree_model_preserves_checked_record_identity(self):
        cpu_id = self.purchase("CPU", "CPU", 100)
        ram_id = self.purchase("RAM", "RAM", 50)
        records = self.services.assemble_snapshot().available_inventory
        model = AssemblyTreeModel()
        model.set_records(records)
        cpu_group = model.index(0, 0)
        cpu = model.index(0, 0, cpu_group)
        self.assertEqual(cpu.data(Qt.ItemDataRole.UserRole), cpu_id)
        self.assertTrue(
            model.setData(
                cpu,
                Qt.CheckState.Checked,
                Qt.ItemDataRole.CheckStateRole,
            )
        )

        model.set_records(tuple(reversed(records)))

        self.assertEqual(model.checked_ids, (cpu_id,))
        self.assertNotIn(ram_id, model.checked_ids)

    def test_edit_dialogs_expose_all_fields_and_same_type_pc_membership(self):
        first_ram = self.purchase("RAM A", "RAM", 40)
        second_ram = self.purchase("RAM B", "RAM", 45)
        added_ram = self.purchase("RAM C", "RAM", 50)
        pc_id = self.services.assemble_pc("Original PC", [first_ram, second_ram])
        snapshot = self.services.inventory_snapshot()
        pc = next(pc for pc in snapshot.pcs if pc.id == pc_id)

        component_dialog = ExpenseEditDialog(pc.parts[0])
        component_dialog.name.setText("Edited RAM")
        component_dialog.item_type.setCurrentText("GPU")
        component_dialog.amount.setText("123.45")
        edited_date = TEST_DATE - timedelta(days=2)
        component_dialog.purchase_date.setDate(
            QDate(edited_date.year, edited_date.month, edited_date.day)
        )
        component_dialog._validate()
        self.assertEqual(
            component_dialog._replacement,
            NewExpense.create(
                "Edited RAM", "GPU", "123.45", TEST_DATE - timedelta(days=2)
            ),
        )

        candidates = tuple(
            part
            for part in snapshot.inventory
            if part.is_available or part.pc_id == pc_id
        )
        pc_dialog = PCEditDialog(pc, candidates)
        self.assertEqual(pc_dialog.tree_model.checked_ids, (first_ram, second_ram))
        self.assertEqual(pc_dialog.tree_model.rowCount(), 1)
        ram_group = pc_dialog.tree_model.index(0, 0)
        self.assertEqual(pc_dialog.tree_model.rowCount(ram_group), 3)
        third_ram = pc_dialog.tree_model.index(2, 0, ram_group)
        self.assertEqual(third_ram.data(Qt.ItemDataRole.UserRole), added_ram)
        pc_dialog.tree_model.setData(
            third_ram,
            Qt.CheckState.Checked,
            Qt.ItemDataRole.CheckStateRole,
        )
        pc_dialog.name.setText("Three RAM PC")
        pc_dialog._validate()
        self.assertEqual(
            pc_dialog._result,
            ("Three RAM PC", (first_ram, second_ram, added_ram)),
        )
        component_dialog.deleteLater()
        pc_dialog.deleteLater()

    def test_database_lock_allows_only_one_application_instance(self):
        database_path = Path(self.temporary_directory.name) / "locked.db"
        first = acquire_instance_lock(database_path)
        second = acquire_instance_lock(database_path)
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        first.unlock()
        third = acquire_instance_lock(database_path)
        self.assertIsNotNone(third)
        third.unlock()

    def test_background_service_keeps_qt_event_loop_responsive(self):
        worker_started = threading.Event()
        release_worker = threading.Event()
        gui_ticks = []
        outcomes = []
        loop = QEventLoop()

        def blocking_operation():
            worker_started.set()
            release_worker.wait(2)
            return "complete"

        manager = TaskManager()
        task = manager.run(
            blocking_operation,
            lambda result: (outcomes.append(result), loop.quit()),
            lambda error: (outcomes.append(error), loop.quit()),
            owner=manager,
        )
        self.assertTrue(worker_started.wait(1))
        QTimer.singleShot(
            0, lambda: (gui_ticks.append("responsive"), release_worker.set())
        )
        QTimer.singleShot(2000, loop.quit)
        loop.exec()

        self.assertEqual(gui_ticks, ["responsive"])
        self.assertEqual(outcomes, ["complete"])
        self.assertIsNotNone(task)
        self.assertFalse(manager.active)

    def test_rejected_thread_pool_submission_is_delivered_as_async_failure(self):
        pool = MagicMock(spec=QThreadPool)
        pool.start.side_effect = RuntimeError("thread pool unavailable")
        tasks = TaskManager(pool=pool)
        failures: list[Exception] = []
        idle_events: list[bool] = []
        tasks.became_idle.connect(lambda: idle_events.append(True))

        with patch("pcims.app.tasks.log_exception") as log:
            tasks.run(
                lambda: 1,
                lambda _result: None,
                failures.append,
                owner=tasks,
            )
            self.assertTrue(tasks.active)
            self.wait_until(lambda: bool(failures))

        self.assertFalse(tasks.active)
        self.assertEqual(str(failures[0]), "thread pool unavailable")
        self.assertEqual(idle_events, [True])
        log.assert_called_once()

    def test_background_result_is_not_delivered_to_a_destroyed_owner(self):
        worker_started = threading.Event()
        release_worker = threading.Event()
        outcomes: list[object] = []
        owner = QWidget()
        manager = TaskManager()

        def blocking_operation():
            worker_started.set()
            release_worker.wait(2)
            return "complete"

        manager.run(
            blocking_operation,
            outcomes.append,
            outcomes.append,
            owner=owner,
        )
        self.assertTrue(worker_started.wait(1))
        delete_qt_object(owner)
        release_worker.set()
        self.wait_until(lambda: not manager.active)

        self.assertEqual(outcomes, [])

    def test_manual_backup_runs_asynchronously_and_restores_button_state(self):
        window = MainWindow(self.services)
        self.wait_for_window(window)
        page = window.settings_page
        loop = QEventLoop()
        original_finished = page._backup_finished

        def finished(backup):
            original_finished(backup)
            loop.quit()

        with (
            patch.object(page, "_backup_finished", side_effect=finished),
            patch("pcims.app.pages.settings.QMessageBox.information") as information,
        ):
            page.create_backup()
            self.assertFalse(page.backup_button.isEnabled())
            self.assertIn("Creating", page.backup_button.text())
            QTimer.singleShot(3000, loop.quit)
            loop.exec()

        self.assertTrue(page.backup_button.isEnabled())
        self.assertEqual(page.backup_button.text(), "Create backup now")
        information.assert_called_once()
        window.deleteLater()

    def test_page_uses_injected_services_not_process_database(self):
        isolated_database = Path(self.temporary_directory.name) / "injected.db"
        services = ApplicationServices(Database.at(isolated_database))
        services.initialize()
        services.add_expenses(
            [NewExpense.create("Injected item", "Extra", 1, TEST_DATE)]
        )
        other_database = Path(self.temporary_directory.name) / "other.db"
        other_services = ApplicationServices(Database.at(other_database))
        other_services.initialize()

        page = InventoryPage(services, tasks=self.tasks)
        page.refresh()
        self.assertEqual(page.parts_model.rowCount(), 1)
        self.assertEqual(page.parts_model.index(0, 1).data(), "Injected item")
        self.assertEqual(other_services.list_expenses(), ())
        page.deleteLater()

    def test_startup_io_failure_is_reported_and_releases_instance_lock(self):
        lock = MagicMock()
        previous_hook = sys.excepthook
        with (
            patch(
                "pcims.app.application.install_exception_hook",
                return_value=previous_hook,
            ),
            patch("pcims.app.application.acquire_instance_lock", return_value=lock),
            patch(
                "pcims.services.ApplicationServices.initialize",
                side_effect=OSError("permission denied"),
            ),
            patch("pcims.app.application.QMessageBox.critical") as critical,
        ):
            result = main([], self.services)

        self.assertEqual(result, 2)
        critical.assert_called_once()
        self.assertIn("permission denied", critical.call_args.args[2])
        lock.unlock.assert_called_once()
        self.assertIs(sys.excepthook, previous_hook)

    def test_instance_lock_permission_failure_is_not_reported_as_another_session(self):
        lock = MagicMock()
        lock.tryLock.return_value = False
        lock.error.return_value = QLockFile.LockError.PermissionError

        class PermissionDeniedLock:
            LockError = QLockFile.LockError

            def __new__(cls, _path):
                return lock

        with (
            patch("pcims.app.application.QLockFile", PermissionDeniedLock),
            self.assertRaisesRegex(PermissionError, "instance lock"),
        ):
            acquire_instance_lock(self.services.database_path)

    def test_unexpected_bootstrap_failure_uses_installed_error_reporter(self):
        lock = MagicMock()
        reporter = MagicMock()
        previous_hook = sys.excepthook

        def install_reporter():
            sys.excepthook = reporter
            return previous_hook

        with (
            patch(
                "pcims.app.application.install_exception_hook",
                side_effect=install_reporter,
            ),
            patch("pcims.app.application.acquire_instance_lock", return_value=lock),
            patch(
                "pcims.app.application.MainWindow",
                side_effect=RuntimeError("simulated bootstrap defect"),
            ),
        ):
            result = main([], self.services)

        self.assertEqual(result, 1)
        reporter.assert_called_once()
        self.assertIs(reporter.call_args.args[0], RuntimeError)
        self.assertIn("simulated bootstrap defect", str(reporter.call_args.args[1]))
        lock.unlock.assert_called_once()
        self.assertIs(sys.excepthook, previous_hook)

    def test_runtime_refresh_failure_is_reported_and_left_retryable(self):
        window = MainWindow(self.services)
        self.wait_for_window(window)
        window.refreshes.mark_dirty(window.inventory_page)
        with (
            patch.object(
                window.inventory_page,
                "load_snapshot",
                side_effect=sqlite3.OperationalError("disk I/O error"),
            ),
            patch("pcims.app.main_window.show_error") as show_error,
        ):
            window.refresh_current()
            self.wait_for_window(window)

        show_error.assert_called_once()
        self.assertTrue(window.refreshes.is_dirty(window.inventory_page))
        window.deleteLater()

    def test_stale_async_refresh_cannot_overwrite_a_newer_result(self):
        window = MainWindow(self.services)
        self.wait_for_window(window)
        first_started = threading.Event()
        release_first = threading.Event()
        call_count = 0

        def load_snapshot():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                first_started.set()
                release_first.wait(2)
                return "old snapshot"
            return "new snapshot"

        window.refreshes.mark_dirty(window.inventory_page)
        with (
            patch.object(
                window.inventory_page, "load_snapshot", side_effect=load_snapshot
            ),
            patch.object(window.inventory_page, "apply_snapshot") as apply_snapshot,
        ):
            window.refresh_current()
            self.wait_until(first_started.is_set)
            for _ in range(25):
                window.refresh_current()
            self.assertEqual(call_count, 1)
            release_first.set()
            self.wait_for_window(window)

        self.assertEqual(call_count, 2)
        self.assertEqual(apply_snapshot.call_args_list, [call("new snapshot")])
        window.deleteLater()

    def test_data_change_invalidates_an_inflight_hidden_page_refresh(self):
        window = MainWindow(self.services)
        self.wait_for_window(window)
        window.tabs.setCurrentWidget(window.purchases_page)
        self.wait_for_window(window)
        inventory_started = threading.Event()
        release_inventory = threading.Event()

        def slow_inventory_snapshot():
            inventory_started.set()
            release_inventory.wait(2)
            return "obsolete inventory"

        window.refreshes.mark_dirty(window.inventory_page)
        with (
            patch.object(
                window.inventory_page,
                "load_snapshot",
                side_effect=slow_inventory_snapshot,
            ),
            patch.object(window.inventory_page, "apply_snapshot") as apply_snapshot,
        ):
            window.refreshes.start(window.inventory_page)
            self.wait_until(inventory_started.is_set)
            window.purchases_page.data_changed.emit()
            release_inventory.set()
            self.wait_for_window(window)

        apply_snapshot.assert_not_called()
        self.assertTrue(window.refreshes.is_dirty(window.inventory_page))
        window.deleteLater()

    def test_database_mutation_keeps_the_qt_event_loop_responsive(self):
        page = PurchasesPage(self.services, tasks=self.tasks)
        page.name.setText("Slow item")
        page.price.setText("1.00")
        page.add_line()
        operation_started = threading.Event()
        release_operation = threading.Event()
        gui_ticks = []

        def slow_add(*_args, **_kwargs):
            operation_started.set()
            release_operation.wait(2)
            return [1]

        with (
            patch(
                "pcims.services.ApplicationServices.add_expenses",
                side_effect=slow_add,
            ),
            patch("pcims.app.pages.purchases.QMessageBox.information"),
        ):
            page.commit_purchase()
            self.wait_until(operation_started.is_set)
            self.assertFalse(page.isEnabled())
            QTimer.singleShot(0, lambda: gui_ticks.append("responsive"))
            self.wait_until(lambda: bool(gui_ticks))
            self.assertTrue(page.command_running)
            release_operation.set()
            self.wait_for_page(page)

        self.assertEqual(gui_ticks, ["responsive"])
        self.assertFalse(page.has_staged_items)
        page.deleteLater()

    def test_unexpected_exception_is_logged_and_reported(self):
        log_path = Path(self.temporary_directory.name) / "errors.log"
        previous = install_exception_hook(log_path)
        try:
            try:
                raise RuntimeError("simulated GUI failure")
            except RuntimeError:
                exception_type, exception, traceback_object = sys.exc_info()
            with patch("pcims.app.errors.QMessageBox.critical") as critical:
                sys.excepthook(exception_type, exception, traceback_object)
            self.assertIn("simulated GUI failure", log_path.read_text(encoding="utf-8"))
            critical.assert_called_once()
        finally:
            sys.excepthook = previous

    def test_error_log_rotation_is_bounded_and_preserves_latest_traceback(self):
        log_path = Path(self.temporary_directory.name) / "rotating-errors.log"
        log_path.write_text("old diagnostic content", encoding="utf-8")
        error = RuntimeError("latest worker failure")

        with patch("pcims.app.errors.MAX_ERROR_LOG_BYTES", 10):
            destination = log_exception(
                type(error), error, error.__traceback__, log_path
            )

        self.assertEqual(destination, log_path.resolve())
        self.assertEqual(
            log_path.with_name(f"{log_path.name}.1").read_text(encoding="utf-8"),
            "old diagnostic content",
        )
        self.assertIn("latest worker failure", log_path.read_text(encoding="utf-8"))

    def test_error_log_appends_when_rotation_target_is_locked(self):
        log_path = Path(self.temporary_directory.name) / "locked-rotation.log"
        log_path.write_text("old diagnostic content", encoding="utf-8")
        error = RuntimeError("traceback must survive")

        with (
            patch("pcims.app.errors.MAX_ERROR_LOG_BYTES", 10),
            patch.object(
                Path,
                "replace",
                side_effect=PermissionError("rotation target locked"),
            ),
        ):
            destination = log_exception(
                type(error), error, error.__traceback__, log_path
            )

        content = log_path.read_text(encoding="utf-8")
        self.assertEqual(destination, log_path.resolve())
        self.assertIn("Log rotation failed: rotation target locked", content)
        self.assertIn("traceback must survive", content)

    def test_error_reporting_survives_an_unavailable_default_data_directory(self):
        error = RuntimeError("fallback diagnostic")

        with (
            patch(
                "pcims.app.errors.ensure_private_directory",
                side_effect=PermissionError("data directory denied"),
            ),
            patch("pcims.app.errors.traceback.print_exception") as print_exception,
        ):
            destination = log_exception(type(error), error, error.__traceback__)

        self.assertIsNone(destination)
        print_exception.assert_called_once_with(type(error), error, error.__traceback__)

    def test_error_reporting_survives_an_invalid_data_directory_override(self):
        error = RuntimeError("invalid configuration diagnostic")

        with (
            patch.dict(os.environ, {"PCIMS_DATA_DIR": "relative-data"}),
            patch("pcims.app.errors.traceback.print_exception") as print_exception,
        ):
            destination = log_exception(type(error), error, error.__traceback__)

        self.assertIsNone(destination)
        print_exception.assert_called_once()

    def test_assemble_page_checks_concrete_ids_and_assembles(self):
        expected_ids = [
            self.purchase("RAM", "RAM", 40),
            self.purchase("RAM", "RAM", 45),
        ]
        page = AssemblePage(self.services, tasks=self.tasks)
        page.refresh()
        page.name.setText("Linux workstation")
        checked = self.check_all_assembly_parts(page)
        page.assemble()
        self.wait_for_page(page)

        self.assertEqual(checked, expected_ids)
        pc = self.services.list_pcs()[0]
        self.assertEqual(pc.name, "Linux workstation")
        self.assertEqual([part.id for part in pc.parts], expected_ids)
        page.deleteLater()

    def test_inventory_sale_and_sales_page_undo_workflow(self):
        ids = [self.purchase("Cable", "Extra", 5), self.purchase("Cable", "Extra", 6)]
        inventory = InventoryPage(self.services, tasks=self.tasks)
        inventory.refresh()
        inventory.parts_table.selectAll()
        with patch(
            "pcims.app.pages.inventory.SaleDialog.get_sale",
            return_value=SaleTerms.create("20.00", TEST_DATE),
        ):
            inventory.sell_selected_parts()
        self.wait_for_page(inventory)

        sale = self.services.list_sales()[0]
        self.assertEqual({item.id for item in sale.items}, set(ids))

        sales = SalesPage(self.services, tasks=self.tasks)
        sales.refresh()
        self.assertEqual(sales.summary_labels["cash"].text(), "€9.00")
        self.assertNotIn("assets", sales.summary_labels)
        sales.sale_table.selectRow(0)
        self.wait_until(lambda: sales.detail_model.rowCount() == 2)
        with patch("pcims.app.pages.sales.ask_confirmation", return_value=True):
            sales.undo_selected()
        self.wait_for_page(sales)

        self.assertEqual(self.services.list_sales(), ())
        self.assertTrue(
            all(expense.is_available for expense in self.services.list_expenses())
        )
        inventory.deleteLater()
        sales.deleteLater()

    def test_sales_purchase_history_pages_without_blocking_the_gui(self):
        self.services.add_expenses(
            NewExpense.create(f"History {index}", "Extra", 1, TEST_DATE)
            for index in range(501)
        )
        page = SalesPage(self.services, tasks=self.tasks)
        page.refresh()

        self.assertEqual(page.expense_model.rowCount(), 500)
        self.assertFalse(page.expense_newer.isEnabled())
        self.assertTrue(page.expense_older.isEnabled())
        page.expense_older.click()
        self.wait_for_page(page)

        self.assertEqual(page.expense_model.rowCount(), 1)
        self.assertEqual(page.expense_model.index(0, 0).data(), "1")
        self.assertTrue(page.expense_newer.isEnabled())
        self.assertFalse(page.expense_older.isEnabled())
        self.assertIn("501–501 of 501", page.expense_page_label.text())
        page.deleteLater()

    def test_sales_page_pages_large_selected_sale_details(self):
        ids = self.services.add_expenses(
            NewExpense.create(f"Bulk {index}", "Extra", 1, TEST_DATE)
            for index in range(501)
        )
        self.services.sell_items(ids, SaleTerms.create(1_000, TEST_DATE))
        page = SalesPage(self.services, tasks=self.tasks)
        page.refresh()

        self.assertEqual(page.sale_model.index(0, 7).data(), "501")
        page.sale_table.selectRow(0)
        self.wait_until(lambda: page.detail_model.rowCount() == 500)
        self.assertTrue(page.detail_older.isEnabled())
        page.detail_older.click()
        self.wait_until(lambda: page._detail_page.offset == 500)

        self.assertEqual(page.detail_model.rowCount(), 1)
        self.assertEqual(page.detail_model.index(0, 0).data(), "501")
        self.assertTrue(page.detail_newer.isEnabled())
        self.assertFalse(page.detail_older.isEnabled())
        page.deleteLater()

    def test_stale_sale_detail_result_cannot_replace_newer_selection(self):
        first_item = self.purchase("First", "Extra", 1)
        second_item = self.purchase("Second", "Extra", 1)
        first_sale = self.services.sell_items(
            [first_item], SaleTerms.create(2, TEST_DATE)
        )
        second_sale = self.services.sell_items(
            [second_item], SaleTerms.create(2, TEST_DATE)
        )
        page = SalesPage(self.services, tasks=self.tasks)
        page.refresh()
        slow_started = threading.Event()
        release_slow = threading.Event()
        original = ApplicationServices.sale_item_page

        def delayed_details(services, sale_id, offset=0, page_size=500):
            if sale_id == second_sale:
                slow_started.set()
                release_slow.wait(2)
            return original(services, sale_id, offset, page_size)

        try:
            with patch.object(
                ApplicationServices, "sale_item_page", new=delayed_details
            ):
                page.sale_table.selectRow(0)
                self.assertTrue(slow_started.wait(1))
                page.sale_table.selectRow(1)
                self.wait_until(
                    lambda: (
                        page._detail_sale_id == first_sale
                        and page.detail_model.rowCount() == 1
                    )
                )
                release_slow.set()
                self.wait_until(lambda: not self.tasks.active)
        finally:
            release_slow.set()

        self.assertEqual(page._detail_sale_id, first_sale)
        self.assertEqual(page.detail_model.index(0, 0).data(), str(first_item))
        page.deleteLater()

    def test_stale_table_selections_fail_closed(self):
        expense_id = self.purchase("Cable", "Extra", 5)
        inventory = InventoryPage(self.services, tasks=self.tasks)
        inventory.refresh()
        inventory.parts_table.selectRow(0)
        inventory._parts.clear()

        with patch("pcims.app.pages.inventory.QMessageBox.information") as information:
            inventory.delete_selected_parts()

        information.assert_called_once()
        self.assertEqual(
            [item.id for item in self.services.list_expenses()], [expense_id]
        )

        self.services.sell_items([expense_id], SaleTerms.create("10.00", TEST_DATE))
        sales = SalesPage(self.services, tasks=self.tasks)
        sales.refresh()
        sales.sale_table.selectRow(0)
        self.wait_until(lambda: not self.tasks.active)
        sales._sales.clear()

        with patch("pcims.app.pages.sales.QMessageBox.information") as information:
            sales.undo_selected()

        information.assert_called_once()
        self.assertEqual(len(self.services.list_sales()), 1)
        inventory.deleteLater()
        sales.deleteLater()

    def test_inventory_full_edit_delete_and_disassemble_actions(self):
        cpu_id = self.purchase("CPU", "CPU", 100)
        spare_id = self.purchase("Spare cable", "Extra", 5)

        inventory = InventoryPage(self.services, tasks=self.tasks)
        inventory.refresh()
        inventory.parts_table.selectRow(0)
        with patch(
            "pcims.app.pages.inventory.ExpenseEditDialog.get_expense",
            return_value=NewExpense.create(
                "Edited GPU", "GPU", 125, TEST_DATE - timedelta(days=1)
            ),
        ):
            inventory.edit_selected_part()
        self.wait_for_page(inventory)
        edited = self.services.list_expenses()[0]
        self.assertEqual(
            (edited.name, edited.item_type, edited.price_cents, edited.purchase_date),
            ("Edited GPU", "GPU", 12_500, TEST_DATE - timedelta(days=1)),
        )

        inventory.refresh()
        spare_row = next(
            row
            for row in range(inventory.parts_model.rowCount())
            if inventory.parts_model.index(row, 0).data() == str(spare_id)
        )
        inventory.parts_table.selectRow(spare_row)
        with patch("pcims.app.pages.inventory.ask_confirmation", return_value=True):
            inventory.delete_selected_parts()
        self.wait_for_page(inventory)
        self.assertEqual([item.id for item in self.services.list_expenses()], [cpu_id])

        assemble = AssemblePage(self.services, tasks=self.tasks)
        assemble.refresh()
        assemble.name.setText("Test PC")
        self.check_all_assembly_parts(assemble)
        assemble.assemble()
        self.wait_for_page(assemble)

        first_ram = self.purchase("RAM A", "RAM", 40)
        second_ram = self.purchase("RAM B", "RAM", 45)
        inventory.refresh()
        inventory.pc_table.selectRow(0)
        with patch(
            "pcims.app.pages.inventory.PCEditDialog.get_pc",
            return_value=("Edited PC", (cpu_id, first_ram, second_ram)),
        ):
            inventory.edit_selected_pc()
        self.wait_for_page(inventory)
        pc = self.services.list_pcs()[0]
        self.assertEqual(pc.name, "Edited PC")
        self.assertEqual(
            tuple(part.id for part in pc.parts), (cpu_id, first_ram, second_ram)
        )
        self.assertEqual([part.item_type for part in pc.parts], ["GPU", "RAM", "RAM"])

        inventory.refresh()
        inventory.pc_table.selectRow(0)
        with patch("pcims.app.pages.inventory.ask_confirmation", return_value=True):
            inventory.disassemble_selected_pc()
        self.wait_for_page(inventory)
        self.assertEqual(self.services.list_pcs(), ())
        self.assertTrue(
            all(part.is_available for part in self.services.list_expenses())
        )
        assemble.deleteLater()
        inventory.deleteLater()

    def test_pc_sale_and_undo_through_qt_pages(self):
        ids = [self.purchase("CPU", "CPU", 100), self.purchase("RAM", "RAM", 50)]
        assemble = AssemblePage(self.services, tasks=self.tasks)
        assemble.refresh()
        assemble.name.setText("PC 1")
        self.check_all_assembly_parts(assemble)
        assemble.assemble()
        self.wait_for_page(assemble)

        inventory = InventoryPage(self.services, tasks=self.tasks)
        inventory.refresh()
        inventory.pc_table.selectRow(0)
        with patch(
            "pcims.app.pages.inventory.SaleDialog.get_sale",
            return_value=SaleTerms.create("200.00", TEST_DATE),
        ):
            inventory.sell_selected_pc()
        self.wait_for_page(inventory)

        self.assertEqual(self.services.list_pcs(), ())
        self.assertEqual(
            {item.id for item in self.services.list_sales()[0].items}, set(ids)
        )

        sales = SalesPage(self.services, tasks=self.tasks)
        sales.refresh()
        sales.sale_table.selectRow(0)
        with patch("pcims.app.pages.sales.ask_confirmation", return_value=True):
            sales.undo_selected()
        self.wait_for_page(sales)
        self.assertEqual([pc.name for pc in self.services.list_pcs()], ["PC 1"])
        self.assertEqual(self.services.list_sales(), ())
        assemble.deleteLater()
        inventory.deleteLater()
        sales.deleteLater()


if __name__ == "__main__":
    unittest.main()
