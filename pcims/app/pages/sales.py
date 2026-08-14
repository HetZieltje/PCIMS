from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from pcims.app.async_page import AsyncCommandPage
from pcims.app.common import ask_confirmation
from pcims.app.formatting import format_cents
from pcims.app.table_model import (
    Column,
    RecordTableModel,
    configure_table_view,
    selected_ids,
)
from pcims.app.tasks import TaskManager
from pcims.contracts import SalesOperations, SalesSnapshot
from pcims.models import Expense, Sale


def _expense_status(item: Expense) -> str:
    if item.sale_id:
        return f"Sold #{item.sale_id}"
    return item.pc_name or "Available"


class SalesPage(AsyncCommandPage):
    data_changed = Signal()

    def __init__(
        self,
        services: SalesOperations,
        *,
        tasks: TaskManager,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(tasks, parent)
        self.services = services
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

        self.expense_model = RecordTableModel[Expense](
            (
                Column("ID", lambda item: str(item.id), lambda item: item.id),
                Column("Name", lambda item: item.name, lambda item: item.name.casefold()),
                Column("Type", lambda item: item.item_type, lambda item: item.item_type),
                Column(
                    "Cost",
                    lambda item: format_cents(item.price_cents),
                    lambda item: item.price_cents,
                ),
                Column(
                    "Purchased",
                    lambda item: item.purchase_date.isoformat(),
                    lambda item: item.purchase_date.toordinal(),
                ),
                Column(
                    "Status", _expense_status, lambda item: _expense_status(item).casefold()
                ),
            ),
            lambda item: item.id,
        )
        self.expense_table = QTableView()
        configure_table_view(self.expense_table, self.expense_model)
        expense_box = QGroupBox("Purchase history")
        expense_layout = QVBoxLayout(expense_box)
        expense_layout.addWidget(self.expense_table)

        self.sale_model = RecordTableModel[Sale](
            (
                Column("ID", lambda sale: str(sale.id), lambda sale: sale.id),
                Column(
                    "Date",
                    lambda sale: sale.sale_date.isoformat(),
                    lambda sale: sale.sale_date.toordinal(),
                ),
                Column("Kind", lambda sale: sale.kind.upper(), lambda sale: sale.kind),
                Column("Name", lambda sale: sale.name, lambda sale: sale.name.casefold()),
                Column(
                    "Cost",
                    lambda sale: format_cents(sale.cost_cents),
                    lambda sale: sale.cost_cents,
                ),
                Column(
                    "Revenue",
                    lambda sale: format_cents(sale.selling_price_cents),
                    lambda sale: sale.selling_price_cents,
                ),
                Column(
                    "Profit",
                    lambda sale: format_cents(sale.profit_cents),
                    lambda sale: sale.profit_cents,
                ),
                Column("Items", lambda sale: str(len(sale.items)), lambda sale: len(sale.items)),
            ),
            lambda sale: sale.id,
        )
        self.sale_table = QTableView()
        configure_table_view(
            self.sale_table,
            self.sale_model,
            stretch_column=3,
        )
        self.sale_table.selectionModel().selectionChanged.connect(
            self._sale_selection_changed
        )
        undo_button = QPushButton("Undo selected sale")
        undo_button.clicked.connect(self.undo_selected)
        sale_actions = QHBoxLayout()
        sale_actions.addStretch()
        sale_actions.addWidget(undo_button)
        sale_box = QGroupBox("Sales")
        sale_layout = QVBoxLayout(sale_box)
        sale_layout.addWidget(self.sale_table)
        sale_layout.addLayout(sale_actions)

        self.detail_model = RecordTableModel[Expense](
            (
                Column("ID", lambda item: str(item.id), lambda item: item.id),
                Column("Name", lambda item: item.name, lambda item: item.name.casefold()),
                Column("Type", lambda item: item.item_type, lambda item: item.item_type),
                Column(
                    "Cost",
                    lambda item: format_cents(item.price_cents),
                    lambda item: item.price_cents,
                ),
                Column(
                    "Purchased",
                    lambda item: item.purchase_date.isoformat(),
                    lambda item: item.purchase_date.toordinal(),
                ),
            ),
            lambda item: item.id,
        )
        self.detail_table = QTableView()
        configure_table_view(self.detail_table, self.detail_model)
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

    def refresh(self) -> None:
        self.apply_snapshot(self.load_snapshot())

    def load_snapshot(self) -> SalesSnapshot:
        return self.services.sales_snapshot()

    def apply_snapshot(self, snapshot: SalesSnapshot) -> None:
        summary = snapshot.summary
        for key, cents in (
            ("expense", summary.expense_cents),
            ("income", summary.income_cents),
            ("profit", summary.profit_cents),
            ("inventory", summary.inventory_cents),
            ("cash", summary.cash_flow_cents),
        ):
            self.summary_labels[key].setText(format_cents(cents))

        self.expense_model.set_records(snapshot.expenses)

        sales = snapshot.sales
        self._sales = {sale.id: sale for sale in sales}
        self.sale_model.set_records(sales)
        self._render_details()

    def _sale_selection_changed(self, *_: object) -> None:
        self._render_details()

    def _render_details(self) -> None:
        ids = selected_ids(self.sale_table)
        items = (
            self._sales[ids[0]].items if len(ids) == 1 and ids[0] in self._sales else ()
        )
        self.detail_model.set_records(items)

    def undo_selected(self) -> None:
        ids = selected_ids(self.sale_table)
        if len(ids) != 1:
            QMessageBox.information(
                self, "Select one sale", "Select exactly one sale to undo."
            )
            return
        sale = self._sales.get(ids[0])
        if sale is None:
            QMessageBox.information(
                self,
                "Selection changed",
                "That sale is no longer in the current view.",
            )
            return
        if not ask_confirmation(self, "Undo sale", f"Undo the sale of '{sale.name}'?"):
            return
        self.run_command(
            lambda: self.services.undo_sale(sale.id),
            self.data_changed.emit,
            "Unable to undo sale",
        )
