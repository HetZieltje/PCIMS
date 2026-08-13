import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, call, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QSettings, Qt, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QMessageBox, QTableView

from pcims.app.application import acquire_instance_lock, create_application, main
from pcims.app.errors import install_exception_hook
from pcims.app.main_window import MainWindow
from pcims.app.pages.assemble import AssemblePage
from pcims.app.pages.inventory import InventoryPage
from pcims.app.pages.purchases import PurchasesPage
from pcims.app.pages.sales import SalesPage
from pcims.app.table_model import (
    Column,
    RecordTableModel,
    configure_table_view,
    selected_ids,
)
from pcims.app.tasks import run_in_background
from pcims.db.backup import BackupResult, create_backup
from pcims.db.connection import configure_database
from pcims.db.queries import (
    add_expenses,
    list_expenses,
    list_pcs,
    list_sales,
)
from pcims.db.schema import initialize_database
from pcims.services import ApplicationServices

TEST_DATE = date(2026, 8, 14)


class QtWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings_directory = tempfile.TemporaryDirectory()
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
        cls.settings_directory.cleanup()

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        settings = QSettings("PCIMS", "PCIMS")
        settings.clear()
        settings.sync()
        configure_database(Path(self.temporary_directory.name) / "qt-test.db")
        initialize_database()

    def tearDown(self):
        self.application.processEvents()
        self.temporary_directory.cleanup()

    def wait_until(self, predicate, timeout=3.0):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            self.application.processEvents()
            time.sleep(0.005)
        self.assertTrue(predicate(), "Timed out waiting for an asynchronous Qt task")

    def wait_for_window(self, window):
        self.wait_until(lambda: not window._refresh_tasks)

    @staticmethod
    def purchase(name, item_type, price):
        return add_expenses(
            [
                {
                    "name": name,
                    "item_type": item_type,
                    "price": price,
                    "purchase_date": TEST_DATE,
                }
            ]
        )[0]

    def test_main_window_constructs_and_refreshes_every_page(self):
        window = MainWindow()
        window.show()
        self.wait_for_window(window)

        self.assertEqual(window.tabs.count(), 5)
        self.assertGreaterEqual(window.width(), 900)
        for index in range(window.tabs.count()):
            window.tabs.setCurrentIndex(index)
            window.refresh_current(index)
            self.wait_for_window(window)
        window.apply_theme("dark")
        window.apply_theme("light")
        window.refresh_all()
        self.wait_for_window(window)
        window.deleteLater()

    def test_page_construction_performs_no_database_io(self):
        services = MagicMock(spec=ApplicationServices)
        pages = (
            InventoryPage(services),
            PurchasesPage(services),
            AssemblePage(services),
            SalesPage(services),
        )

        self.assertEqual(services.mock_calls, [])
        for page in pages:
            page.deleteLater()

    def test_data_changes_refresh_only_visible_page_until_tab_is_opened(self):
        window = MainWindow()
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
        page = InventoryPage()
        page.refresh()
        with (
            patch("pcims.services.ApplicationServices.list_inventory") as inventory_query,
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
        first = MainWindow()
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
        first._save_window_state()
        self.assertEqual(
            QSettings("PCIMS", "PCIMS").value("window/geometry"), expected_geometry
        )
        first.deleteLater()

        with patch.object(MainWindow, "restoreGeometry", return_value=True) as restore:
            second = MainWindow()
            self.wait_for_window(second)
        restore.assert_called_once_with(expected_geometry)
        second.show()
        self.application.processEvents()
        self.assertEqual(second.tabs.currentIndex(), 3)
        self.assertEqual(
            (
                second.inventory_page.splitter.saveState(),
                second.sales_page.splitter.saveState(),
                second.sales_page.detail_splitter.saveState(),
            ),
            expected_splitters,
        )
        second.deleteLater()

    def test_purchase_page_allocates_quantity_total_and_commits(self):
        page = PurchasesPage()
        page.refresh()
        page.name.setText("Case fan")
        page.type.setCurrentText("Fan")
        page.quantity.setValue(3)
        page.price.setText("10,00")
        page.total_for_quantity.setChecked(True)
        page.add_line()

        self.assertEqual(len(page._staged), 3)
        self.assertEqual(
            [item["price_cents"] for item in page._staged], [334, 333, 333]
        )
        with patch("pcims.app.pages.purchases.QMessageBox.information"):
            page.commit_purchase()

        self.assertEqual(
            [item.price_cents for item in list_expenses()], [334, 333, 333]
        )
        page.deleteLater()

    def test_purchase_page_reports_database_failure_without_losing_staged_work(self):
        page = PurchasesPage()
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
            patch("pcims.app.pages.purchases.show_error") as show_error,
        ):
            page.commit_purchase()

        self.assertTrue(page.has_staged_items)
        show_error.assert_called_once()
        self.assertIn("simulated disk failure", str(show_error.call_args.args[2]))
        page.deleteLater()

    def test_close_warns_before_discarding_staged_purchase(self):
        window = MainWindow()
        self.wait_for_window(window)
        window.purchases_page._staged.append({"staged_id": 1})
        event = QCloseEvent()
        with patch(
            "pcims.app.main_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ):
            window.closeEvent(event)
        self.assertFalse(event.isAccepted())
        window.deleteLater()

    def test_close_accepts_verified_backup_with_retention_warning(self):
        window = MainWindow()
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
        window = MainWindow()
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
        self.assertTrue(window.isEnabled())
        self.assertTrue(window.isVisible())
        question.assert_called_once()
        window.deleteLater()

    def test_restore_discards_staged_purchase_only_after_success(self):
        self.purchase("Backup item", "CPU", 100)
        backup = create_backup(Path(self.temporary_directory.name) / "backups")
        self.purchase("Later item", "RAM", 50)
        window = MainWindow()
        self.wait_for_window(window)
        window.purchases_page._staged.append({"staged_id": 1})
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

        self.assertIn("Unrecorded purchase lines", confirm.call_args.args[2])
        self.assertTrue(window.isEnabled())
        self.assertFalse(window.purchases_page.has_staged_items)
        self.assertEqual([item.name for item in list_expenses()], ["Backup item"])
        window.deleteLater()

    def test_failed_async_restore_reenables_window_and_preserves_work(self):
        self.purchase("Existing item", "Extra", 10)
        window = MainWindow()
        self.wait_for_window(window)
        window.purchases_page._staged.append({"staged_id": 1})
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
        self.assertEqual([item.name for item in list_expenses()], ["Existing item"])
        self.assertEqual(window.settings_page.restore_button.text(), "Restore backup…")
        show_error.assert_called_once()
        window.deleteLater()

    def test_table_model_sorts_by_typed_values(self):
        model = RecordTableModel[tuple[int, str]](
            (Column("Price", lambda item: item[1], lambda item: item[0]),),
            lambda item: item[0],
        )
        model.set_records(
            ((10000, "€100.00"), (900, "€9.00"), (2000, "€20.00"))
        )
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
        model.set_records(tuple((number, f"Item {number}") for number in range(9999, -1, -1)))

        self.assertEqual(model.rowCount(), 10_000)
        self.assertEqual(model.index(0, 0).data(), "0")
        self.assertEqual(model.index(9999, 0).data(), "9999")

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

        task = run_in_background(
            blocking_operation,
            lambda result: (outcomes.append(result), loop.quit()),
            lambda error: (outcomes.append(error), loop.quit()),
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

    def test_manual_backup_runs_asynchronously_and_restores_button_state(self):
        window = MainWindow()
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
        services = ApplicationServices(configure_database(isolated_database))
        services.initialize()
        services.add_expenses(
            [
                {
                    "name": "Injected item",
                    "item_type": "Extra",
                    "price": 1,
                    "purchase_date": TEST_DATE,
                }
            ]
        )
        other_database = Path(self.temporary_directory.name) / "other.db"
        configure_database(other_database)
        initialize_database()

        page = InventoryPage(services)
        page.refresh()
        self.assertEqual(page.parts_model.rowCount(), 1)
        self.assertEqual(page.parts_model.index(0, 1).data(), "Injected item")
        self.assertEqual(list_expenses(), ())
        page.deleteLater()

    def test_startup_io_failure_is_reported_and_releases_instance_lock(self):
        lock = MagicMock()
        with (
            patch("pcims.app.application.install_exception_hook"),
            patch("pcims.app.application.acquire_instance_lock", return_value=lock),
            patch(
                "pcims.services.ApplicationServices.initialize",
                side_effect=OSError("permission denied"),
            ),
            patch("pcims.app.application.QMessageBox.critical") as critical,
        ):
            result = main([])

        self.assertEqual(result, 2)
        critical.assert_called_once()
        self.assertIn("permission denied", critical.call_args.args[2])
        lock.unlock.assert_called_once()

    def test_runtime_refresh_failure_is_reported_and_left_retryable(self):
        window = MainWindow()
        self.wait_for_window(window)
        window._dirty_pages.add(window.inventory_page)
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
        self.assertIn(window.inventory_page, window._dirty_pages)
        window.deleteLater()

    def test_stale_async_refresh_cannot_overwrite_a_newer_result(self):
        window = MainWindow()
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

        window._dirty_pages.add(window.inventory_page)
        with (
            patch.object(
                window.inventory_page, "load_snapshot", side_effect=load_snapshot
            ),
            patch.object(window.inventory_page, "apply_snapshot") as apply_snapshot,
        ):
            window.refresh_current()
            self.wait_until(first_started.is_set)
            window.refresh_current()
            self.wait_until(lambda: apply_snapshot.call_count == 1)
            release_first.set()
            self.wait_for_window(window)

        self.assertEqual(apply_snapshot.call_args_list, [call("new snapshot")])
        window.deleteLater()

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

    def test_assemble_page_checks_concrete_ids_and_assembles(self):
        expected_ids = [
            self.purchase("RAM", "RAM", 40),
            self.purchase("RAM", "RAM", 45),
        ]
        page = AssemblePage()
        page.refresh()
        page.name.setText("Linux workstation")
        checked = []
        for group_index in range(page.tree.topLevelItemCount()):
            group = page.tree.topLevelItem(group_index)
            for child_index in range(group.childCount()):
                child = group.child(child_index)
                child.setCheckState(0, Qt.CheckState.Checked)
                checked.append(child.data(0, Qt.ItemDataRole.UserRole))
        page.assemble()

        self.assertEqual(checked, expected_ids)
        pc = list_pcs()[0]
        self.assertEqual(pc.name, "Linux workstation")
        self.assertEqual([part.id for part in pc.parts], expected_ids)
        page.deleteLater()

    def test_inventory_sale_and_sales_page_undo_workflow(self):
        ids = [self.purchase("Cable", "Extra", 5), self.purchase("Cable", "Extra", 6)]
        inventory = InventoryPage()
        inventory.refresh()
        inventory.parts_table.selectAll()
        with patch(
            "pcims.app.pages.inventory.SaleDialog.get_sale",
            return_value=(Decimal("20.00"), TEST_DATE),
        ):
            inventory.sell_selected_parts()

        sale = list_sales()[0]
        self.assertEqual({item.id for item in sale.items}, set(ids))

        sales = SalesPage()
        sales.refresh()
        self.assertEqual(sales.summary_labels["cash"].text(), "€9.00")
        self.assertNotIn("assets", sales.summary_labels)
        sales.sale_table.selectRow(0)
        with patch("pcims.app.pages.sales.ask_confirmation", return_value=True):
            sales.undo_selected()

        self.assertEqual(list_sales(), ())
        self.assertTrue(all(expense.is_available for expense in list_expenses()))
        inventory.deleteLater()
        sales.deleteLater()

    def test_inventory_rename_delete_and_disassemble_actions(self):
        cpu_id = self.purchase("CPU", "CPU", 100)
        spare_id = self.purchase("Spare cable", "Extra", 5)

        inventory = InventoryPage()
        inventory.refresh()
        inventory.parts_table.selectRow(0)
        with patch(
            "pcims.app.pages.inventory.QInputDialog.getText",
            return_value=("Renamed CPU", True),
        ):
            inventory.rename_selected_parts()
        self.assertEqual(list_expenses()[0].name, "Renamed CPU")

        inventory.refresh()
        spare_row = next(
            row
            for row in range(inventory.parts_model.rowCount())
            if inventory.parts_model.index(row, 0).data() == str(spare_id)
        )
        inventory.parts_table.selectRow(spare_row)
        with patch("pcims.app.pages.inventory.ask_confirmation", return_value=True):
            inventory.delete_selected_parts()
        self.assertEqual([item.id for item in list_expenses()], [cpu_id])

        assemble = AssemblePage()
        assemble.refresh()
        assemble.name.setText("Test PC")
        assemble.tree.topLevelItem(0).child(0).setCheckState(0, Qt.CheckState.Checked)
        assemble.assemble()

        inventory.refresh()
        inventory.pc_table.selectRow(0)
        with patch(
            "pcims.app.pages.inventory.QInputDialog.getText",
            return_value=("Renamed PC", True),
        ):
            inventory.rename_selected_pc()
        self.assertEqual(list_pcs()[0].name, "Renamed PC")

        inventory.refresh()
        inventory.pc_table.selectRow(0)
        with patch("pcims.app.pages.inventory.ask_confirmation", return_value=True):
            inventory.disassemble_selected_pc()
        self.assertEqual(list_pcs(), ())
        self.assertTrue(list_expenses()[0].is_available)
        assemble.deleteLater()
        inventory.deleteLater()

    def test_pc_sale_and_undo_through_qt_pages(self):
        ids = [self.purchase("CPU", "CPU", 100), self.purchase("RAM", "RAM", 50)]
        assemble = AssemblePage()
        assemble.refresh()
        assemble.name.setText("PC 1")
        for group_index in range(assemble.tree.topLevelItemCount()):
            group = assemble.tree.topLevelItem(group_index)
            for child_index in range(group.childCount()):
                group.child(child_index).setCheckState(0, Qt.CheckState.Checked)
        assemble.assemble()

        inventory = InventoryPage()
        inventory.refresh()
        inventory.pc_table.selectRow(0)
        with patch(
            "pcims.app.pages.inventory.SaleDialog.get_sale",
            return_value=(Decimal("200.00"), TEST_DATE),
        ):
            inventory.sell_selected_pc()

        self.assertEqual(list_pcs(), ())
        self.assertEqual({item.id for item in list_sales()[0].items}, set(ids))

        sales = SalesPage()
        sales.refresh()
        sales.sale_table.selectRow(0)
        with patch("pcims.app.pages.sales.ask_confirmation", return_value=True):
            sales.undo_selected()
        self.assertEqual([pc.name for pc in list_pcs()], ["PC 1"])
        self.assertEqual(list_sales(), ())
        assemble.deleteLater()
        inventory.deleteLater()
        sales.deleteLater()


if __name__ == "__main__":
    unittest.main()
