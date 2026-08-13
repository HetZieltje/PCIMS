import os
import sys
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QMessageBox,
    QTableWidget,
    QTableWidgetSelectionRange,
)

from app.application import acquire_instance_lock, create_application
from app.common import configure_table, table_item
from app.errors import install_exception_hook
from app.main_window import MainWindow
from app.pages.assemble import AssemblePage
from app.pages.inventory import InventoryPage
from app.pages.purchases import PurchasesPage
from app.pages.sales import SalesPage
from db.connection import configure_database
from db.queries import (
    add_expenses,
    initialize_database,
    list_expenses,
    list_pcs,
    list_sales,
)


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
        configure_database(Path(self.temporary_directory.name) / "qt-test.db")
        initialize_database()

    def tearDown(self):
        self.application.processEvents()
        self.temporary_directory.cleanup()

    @staticmethod
    def purchase(name, item_type, price):
        return add_expenses(
            [
                {
                    "name": name,
                    "item_type": item_type,
                    "price": price,
                    "purchase_date": date.today(),
                }
            ]
        )[0]

    def test_main_window_constructs_and_refreshes_every_page(self):
        window = MainWindow()
        window.show()
        self.application.processEvents()

        self.assertEqual(window.tabs.count(), 5)
        self.assertGreaterEqual(window.width(), 900)
        for index in range(window.tabs.count()):
            window.tabs.setCurrentIndex(index)
            window.refresh_current(index)
        window.apply_theme("dark")
        window.apply_theme("light")
        window.refresh_all()
        self.application.processEvents()
        window.deleteLater()

    def test_purchase_page_allocates_quantity_total_and_commits(self):
        page = PurchasesPage()
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
        with patch("app.pages.purchases.QMessageBox.information"):
            page.commit_purchase()

        self.assertEqual(
            [item.price_cents for item in list_expenses()], [334, 333, 333]
        )
        page.deleteLater()

    def test_close_warns_before_discarding_staged_purchase(self):
        window = MainWindow()
        window.purchases_page._staged.append({"staged_id": 1})
        event = QCloseEvent()
        with patch(
            "app.main_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ):
            window.closeEvent(event)
        self.assertFalse(event.isAccepted())
        window.deleteLater()

    def test_table_items_sort_by_typed_values(self):
        table = QTableWidget()
        configure_table(table, ("Price",), stretch_column=-1)
        table.setSortingEnabled(False)
        table.setRowCount(3)
        for row, (text, cents) in enumerate(
            (("€100.00", 10000), ("€9.00", 900), ("€20.00", 2000))
        ):
            table.setItem(row, 0, table_item(text, sort_value=cents))
        table.setSortingEnabled(True)
        table.sortItems(0, Qt.SortOrder.AscendingOrder)
        self.assertEqual(
            [table.item(row, 0).text() for row in range(3)],
            ["€9.00", "€20.00", "€100.00"],
        )
        table.deleteLater()

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

    def test_unexpected_exception_is_logged_and_reported(self):
        log_path = Path(self.temporary_directory.name) / "errors.log"
        previous = install_exception_hook(log_path)
        try:
            try:
                raise RuntimeError("simulated GUI failure")
            except RuntimeError:
                exception_type, exception, traceback_object = sys.exc_info()
            with patch("app.errors.QMessageBox.critical") as critical:
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
        inventory.parts_table.setRangeSelected(
            QTableWidgetSelectionRange(
                0,
                0,
                inventory.parts_table.rowCount() - 1,
                inventory.parts_table.columnCount() - 1,
            ),
            True,
        )
        with patch(
            "app.pages.inventory.SaleDialog.get_sale",
            return_value=(Decimal("20.00"), date.today()),
        ):
            inventory.sell_selected_parts()

        sale = list_sales()[0]
        self.assertEqual({item.id for item in sale.items}, set(ids))

        sales = SalesPage()
        sales.sale_table.selectRow(0)
        with patch("app.pages.sales.ask_confirmation", return_value=True):
            sales.undo_selected()

        self.assertEqual(list_sales(), ())
        self.assertTrue(all(expense.is_available for expense in list_expenses()))
        inventory.deleteLater()
        sales.deleteLater()

    def test_pc_sale_and_undo_through_qt_pages(self):
        ids = [self.purchase("CPU", "CPU", 100), self.purchase("RAM", "RAM", 50)]
        assemble = AssemblePage()
        assemble.name.setText("PC 1")
        for group_index in range(assemble.tree.topLevelItemCount()):
            group = assemble.tree.topLevelItem(group_index)
            for child_index in range(group.childCount()):
                group.child(child_index).setCheckState(0, Qt.CheckState.Checked)
        assemble.assemble()

        inventory = InventoryPage()
        inventory.pc_table.selectRow(0)
        with patch(
            "app.pages.inventory.SaleDialog.get_sale",
            return_value=(Decimal("200.00"), date.today()),
        ):
            inventory.sell_selected_pc()

        self.assertEqual(list_pcs(), ())
        self.assertEqual({item.id for item in list_sales()[0].items}, set(ids))

        sales = SalesPage()
        sales.sale_table.selectRow(0)
        with patch("app.pages.sales.ask_confirmation", return_value=True):
            sales.undo_selected()
        self.assertEqual([pc.name for pc in list_pcs()], ["PC 1"])
        self.assertEqual(list_sales(), ())
        assemble.deleteLater()
        inventory.deleteLater()
        sales.deleteLater()


if __name__ == "__main__":
    unittest.main()
