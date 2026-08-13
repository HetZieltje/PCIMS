from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from pcims.app.common import (
    DATA_OPERATION_ERRORS,
    ask_confirmation,
    configure_table,
    selected_ids,
    show_error,
    table_item,
)
from pcims.app.formatting import format_cents
from pcims.db.models import Sale
from pcims.services import ApplicationServices, default_services


class SalesPage(QWidget):
    data_changed = Signal()

    def __init__(
        self,
        services: ApplicationServices | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.services = services or default_services()
        self._sales: dict[int, Sale] = {}
        self.summary_labels: dict[str, QLabel] = {}
        summary_box = QGroupBox("Financial summary")
        summary_layout = QGridLayout(summary_box)
        for column, (key, title) in enumerate(
            (
                ("expense", "Total purchases"),
                ("income", "Sales revenue"),
                ("profit", "Realized profit"),
                ("inventory", "Inventory value"),
                ("cash", "Cash flow"),
            )
        ):
            title_label = QLabel(title)
            value_label = QLabel("€0.00")
            value_label.setStyleSheet("font-size: 18px; font-weight: 600")
            summary_layout.addWidget(title_label, 0, column)
            summary_layout.addWidget(value_label, 1, column)
            self.summary_labels[key] = value_label

        self.expense_table = QTableWidget()
        configure_table(
            self.expense_table,
            ("ID", "Name", "Type", "Cost", "Purchased", "Status"),
        )
        expense_box = QGroupBox("Purchase history")
        expense_layout = QVBoxLayout(expense_box)
        expense_layout.addWidget(self.expense_table)

        self.sale_table = QTableWidget()
        configure_table(
            self.sale_table,
            ("ID", "Date", "Kind", "Name", "Cost", "Revenue", "Profit", "Items"),
            stretch_column=3,
        )
        self.sale_table.itemSelectionChanged.connect(self._render_details)
        undo_button = QPushButton("Undo selected sale")
        undo_button.clicked.connect(self.undo_selected)
        sale_actions = QHBoxLayout()
        sale_actions.addStretch()
        sale_actions.addWidget(undo_button)
        sale_box = QGroupBox("Sales")
        sale_layout = QVBoxLayout(sale_box)
        sale_layout.addWidget(self.sale_table)
        sale_layout.addLayout(sale_actions)

        self.detail_table = QTableWidget()
        configure_table(self.detail_table, ("ID", "Name", "Type", "Cost", "Purchased"))
        detail_box = QGroupBox("Selected sale items")
        detail_layout = QVBoxLayout(detail_box)
        detail_layout.addWidget(self.detail_table)

        self.detail_splitter = QSplitter()
        self.detail_splitter.setOrientation(Qt.Orientation.Vertical)
        self.detail_splitter.addWidget(sale_box)
        self.detail_splitter.addWidget(detail_box)
        self.detail_splitter.setSizes((400, 220))
        self.splitter = QSplitter()
        self.splitter.addWidget(expense_box)
        self.splitter.addWidget(self.detail_splitter)
        self.splitter.setSizes((520, 680))

        layout = QVBoxLayout(self)
        layout.addWidget(summary_box)
        layout.addWidget(self.splitter, 1)
        self.refresh()

    def refresh(self) -> None:
        summary = self.services.financial_summary()
        for key, cents in (
            ("expense", summary.expense_cents),
            ("income", summary.income_cents),
            ("profit", summary.profit_cents),
            ("inventory", summary.inventory_cents),
            ("cash", summary.cash_flow_cents),
        ):
            self.summary_labels[key].setText(format_cents(cents))

        expenses = self.services.list_expenses()
        self.expense_table.setSortingEnabled(False)
        self.expense_table.setRowCount(len(expenses))
        for row, item in enumerate(expenses):
            status = (
                f"Sold #{item.sale_id}"
                if item.sale_id
                else (item.pc_name or "Available")
            )
            expense_values: tuple[object, ...] = (
                item.id,
                item.name,
                item.item_type,
                format_cents(item.price_cents),
                item.purchase_date.isoformat(),
                status,
            )
            expense_sort_values: tuple[object, ...] = (
                item.id,
                item.name.casefold(),
                item.item_type,
                item.price_cents,
                item.purchase_date.toordinal(),
                status.casefold(),
            )
            for column, value in enumerate(expense_values):
                self.expense_table.setItem(
                    row,
                    column,
                    table_item(
                        value,
                        item.id if column == 0 else None,
                        sort_value=expense_sort_values[column],
                    ),
                )
        self.expense_table.setSortingEnabled(True)

        sales = self.services.list_sales()
        self._sales = {sale.id: sale for sale in sales}
        self.sale_table.setSortingEnabled(False)
        self.sale_table.setRowCount(len(sales))
        for row, sale in enumerate(sales):
            sale_values: tuple[object, ...] = (
                sale.id,
                sale.sale_date.isoformat(),
                sale.kind.upper(),
                sale.name,
                format_cents(sale.cost_cents),
                format_cents(sale.selling_price_cents),
                format_cents(sale.profit_cents),
                len(sale.items),
            )
            sale_sort_values: tuple[object, ...] = (
                sale.id,
                sale.sale_date.toordinal(),
                sale.kind,
                sale.name.casefold(),
                sale.cost_cents,
                sale.selling_price_cents,
                sale.profit_cents,
                len(sale.items),
            )
            for column, value in enumerate(sale_values):
                self.sale_table.setItem(
                    row,
                    column,
                    table_item(
                        value,
                        sale.id if column == 0 else None,
                        sort_value=sale_sort_values[column],
                    ),
                )
        self.sale_table.setSortingEnabled(True)
        self._render_details()

    def _render_details(self) -> None:
        ids = selected_ids(self.sale_table)
        items = (
            self._sales[ids[0]].items if len(ids) == 1 and ids[0] in self._sales else ()
        )
        self.detail_table.setSortingEnabled(False)
        self.detail_table.setRowCount(len(items))
        for row, item in enumerate(items):
            detail_values: tuple[object, ...] = (
                item.id,
                item.name,
                item.item_type,
                format_cents(item.price_cents),
                item.purchase_date.isoformat(),
            )
            detail_sort_values: tuple[object, ...] = (
                item.id,
                item.name.casefold(),
                item.item_type,
                item.price_cents,
                item.purchase_date.toordinal(),
            )
            for column, value in enumerate(detail_values):
                self.detail_table.setItem(
                    row,
                    column,
                    table_item(
                        value,
                        item.id if column == 0 else None,
                        sort_value=detail_sort_values[column],
                    ),
                )
        self.detail_table.setSortingEnabled(True)

    def undo_selected(self) -> None:
        ids = selected_ids(self.sale_table)
        if len(ids) != 1:
            QMessageBox.information(
                self, "Select one sale", "Select exactly one sale to undo."
            )
            return
        sale = self._sales[ids[0]]
        if not ask_confirmation(self, "Undo sale", f"Undo the sale of '{sale.name}'?"):
            return
        try:
            self.services.undo_sale(sale.id)
        except DATA_OPERATION_ERRORS as error:
            show_error(self, "Unable to undo sale", error)
            return
        self.data_changed.emit()
