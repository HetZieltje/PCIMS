from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from pcims.app.async_page import AsyncCommandPage
from pcims.app.common import ask_confirmation, show_error
from pcims.app.dialogs import ProofEditDialog, SaleDialog
from pcims.app.formatting import format_cents, format_percentage_basis_points
from pcims.app.table_model import (
    Column,
    ContextAction,
    RecordTableModel,
    configure_context_menu,
    configure_table_view,
    selected_ids,
)
from pcims.app.tasks import TaskManager
from pcims.contracts import HistoryPage, SalesOperations, SalesSnapshot
from pcims.domain import SaleTerms
from pcims.models import Expense, SaleSummary


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
        self._expenses: dict[int, Expense] = {}
        self._sales: dict[int, SaleSummary] = {}
        self._expense_page = HistoryPage[Expense]((), 0, 0, 500)
        self._sale_page = HistoryPage[SaleSummary]((), 0, 0, 500)
        self._detail_page = HistoryPage[Expense]((), 0, 0, 500)
        self._detail_sale_id: int | None = None
        self._detail_generation = 0
        self.search = QLineEdit()
        self.search.setPlaceholderText(
            "Search purchases, serial numbers, vendors, locations, or sales…"
        )
        self.search.returnPressed.connect(self.apply_filter)
        search_button = QPushButton("Search")
        search_button.clicked.connect(self.apply_filter)
        clear_button = QPushButton("Clear")
        clear_button.clicked.connect(self.clear_filter)
        search_row = QHBoxLayout()
        search_row.addWidget(self.search, 1)
        search_row.addWidget(search_button)
        search_row.addWidget(clear_button)
        self.summary_labels: dict[str, QLabel] = {}
        summary_box = QGroupBox("Financial summary")
        summary_layout = QGridLayout(summary_box)
        for column, (key, title) in enumerate(
            (
                ("expense", "Total purchases"),
                ("income", "Sales revenue"),
                ("profit", "Realized profit"),
                ("roi", "Realized ROI"),
                ("inventory", "Inventory value"),
                ("cash", "Cash flow"),
            )
        ):
            title_label = QLabel(title)
            value_label = QLabel("N/A" if key == "roi" else "€0.00")
            value_label.setStyleSheet("font-size: 18px; font-weight: 600")
            if key == "roi":
                explanation = "Realized ROI = realized profit ÷ cost of sold items."
                title_label.setToolTip(explanation)
                value_label.setToolTip(explanation)
            summary_layout.addWidget(title_label, 0, column)
            summary_layout.addWidget(value_label, 1, column)
            self.summary_labels[key] = value_label

        self.expense_model = RecordTableModel[Expense](
            (
                Column("ID", lambda item: str(item.id), lambda item: item.id),
                Column(
                    "Name", lambda item: item.name, lambda item: item.name.casefold()
                ),
                Column(
                    "Type",
                    lambda item: item.display_type,
                    lambda item: item.display_type,
                ),
                Column(
                    "Cost",
                    lambda item: format_cents(item.purchase_cost_cents),
                    lambda item: item.purchase_cost_cents,
                ),
                Column(
                    "Purchased",
                    lambda item: item.purchase_date.isoformat(),
                    lambda item: item.purchase_date.toordinal(),
                ),
                Column(
                    "Status",
                    _expense_status,
                    lambda item: _expense_status(item).casefold(),
                ),
                Column(
                    "Proofs",
                    lambda item: str(len(item.proofs)),
                    lambda item: len(item.proofs),
                ),
            ),
            lambda item: item.id,
        )
        self.expense_table = QTableView()
        configure_table_view(self.expense_table, self.expense_model)
        configure_context_menu(
            self.expense_table,
            (
                ContextAction(
                    "Proofs of purchase…",
                    self.edit_selected_expense_proofs,
                    lambda: self._selected_expense(self.expense_table) is not None,
                ),
            ),
        )
        self.expense_table.sortByColumn(0, Qt.SortOrder.DescendingOrder)
        self.expense_newer = QPushButton("Newer")
        self.expense_newer.clicked.connect(lambda: self._change_expense_page(-1))
        self.expense_page_label = QLabel("0 records")
        self.expense_older = QPushButton("Older")
        self.expense_older.clicked.connect(lambda: self._change_expense_page(1))
        expense_navigation = QHBoxLayout()
        expense_navigation.addWidget(self.expense_newer)
        expense_navigation.addWidget(self.expense_page_label)
        expense_navigation.addWidget(self.expense_older)
        expense_navigation.addStretch()
        expense_proofs = QPushButton("Proofs…")
        expense_proofs.clicked.connect(self.edit_selected_expense_proofs)
        expense_navigation.addWidget(expense_proofs)
        expense_box = QGroupBox("Purchase history")
        expense_layout = QVBoxLayout(expense_box)
        expense_layout.addWidget(self.expense_table)
        expense_layout.addLayout(expense_navigation)

        self.sale_model = RecordTableModel[SaleSummary](
            (
                Column("ID", lambda sale: str(sale.id), lambda sale: sale.id),
                Column(
                    "Date",
                    lambda sale: sale.sale_date.isoformat(),
                    lambda sale: sale.sale_date.toordinal(),
                ),
                Column("Kind", lambda sale: sale.kind.upper(), lambda sale: sale.kind),
                Column(
                    "Name", lambda sale: sale.name, lambda sale: sale.name.casefold()
                ),
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
                Column(
                    "ROI on cost",
                    lambda sale: format_percentage_basis_points(sale.roi_basis_points),
                    lambda sale: (
                        sale.roi_basis_points
                        if sale.roi_basis_points is not None
                        else -10_001
                    ),
                ),
                Column(
                    "Items",
                    lambda sale: str(sale.item_count),
                    lambda sale: sale.item_count,
                ),
            ),
            lambda sale: sale.id,
        )
        self.sale_table = QTableView()
        configure_table_view(
            self.sale_table,
            self.sale_model,
            stretch_column=3,
        )
        self.sale_table.sortByColumn(0, Qt.SortOrder.DescendingOrder)
        configure_context_menu(
            self.sale_table,
            (
                ContextAction(
                    "Edit sale…",
                    self.edit_selected_sale,
                    self._has_one_selected_sale,
                ),
                ContextAction(
                    "Undo sale…",
                    self.undo_selected,
                    self._has_one_selected_sale,
                    separator_before=True,
                ),
            ),
        )
        self.sale_table.selectionModel().selectionChanged.connect(
            self._sale_selection_changed
        )
        undo_button = QPushButton("Undo selected sale")
        undo_button.clicked.connect(self.undo_selected)
        edit_button = QPushButton("Edit selected sale…")
        edit_button.clicked.connect(self.edit_selected_sale)
        self.sale_newer = QPushButton("Newer")
        self.sale_newer.clicked.connect(lambda: self._change_sale_page(-1))
        self.sale_page_label = QLabel("0 records")
        self.sale_older = QPushButton("Older")
        self.sale_older.clicked.connect(lambda: self._change_sale_page(1))
        sale_actions = QHBoxLayout()
        sale_actions.addWidget(self.sale_newer)
        sale_actions.addWidget(self.sale_page_label)
        sale_actions.addWidget(self.sale_older)
        sale_actions.addStretch()
        sale_actions.addWidget(edit_button)
        sale_actions.addWidget(undo_button)
        sale_box = QGroupBox("Sales")
        sale_layout = QVBoxLayout(sale_box)
        sale_layout.addWidget(self.sale_table)
        sale_layout.addLayout(sale_actions)

        self.detail_model = RecordTableModel[Expense](
            (
                Column("ID", lambda item: str(item.id), lambda item: item.id),
                Column(
                    "Name", lambda item: item.name, lambda item: item.name.casefold()
                ),
                Column(
                    "Type",
                    lambda item: item.display_type,
                    lambda item: item.display_type,
                ),
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
        configure_context_menu(
            self.detail_table,
            (
                ContextAction(
                    "Proofs of purchase…",
                    self.edit_selected_detail_proofs,
                    lambda: self._selected_expense(self.detail_table) is not None,
                ),
            ),
        )
        self.detail_newer = QPushButton("Newer")
        self.detail_newer.clicked.connect(lambda: self._change_detail_page(-1))
        self.detail_page_label = QLabel("Select one sale")
        self.detail_older = QPushButton("Older")
        self.detail_older.clicked.connect(lambda: self._change_detail_page(1))
        detail_navigation = QHBoxLayout()
        detail_navigation.addWidget(self.detail_newer)
        detail_navigation.addWidget(self.detail_page_label)
        detail_navigation.addWidget(self.detail_older)
        detail_navigation.addStretch()
        detail_box = QGroupBox("Selected sale items")
        detail_layout = QVBoxLayout(detail_box)
        detail_layout.addWidget(self.detail_table)
        detail_layout.addLayout(detail_navigation)
        self._clear_details()

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
        layout.addLayout(search_row)
        layout.addWidget(self.splitter, 1)

    def refresh(self) -> None:
        self.apply_snapshot(self.load_snapshot())

    def load_snapshot(self) -> SalesSnapshot:
        return self.services.sales_snapshot(
            self._expense_page.offset,
            self._sale_page.offset,
            self._expense_page.limit,
            self.search.text(),
        )

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
        self.summary_labels["roi"].setText(
            format_percentage_basis_points(summary.roi_basis_points)
        )

        self._expense_page = snapshot.expenses
        self._expenses = {item.id: item for item in snapshot.expenses.records}
        self.expense_model.set_records(snapshot.expenses.records)
        self.expense_page_label.setText(self._page_label(snapshot.expenses))
        self.expense_newer.setEnabled(snapshot.expenses.has_previous)
        self.expense_older.setEnabled(snapshot.expenses.has_next)

        self._sale_page = snapshot.sales
        sales = snapshot.sales.records
        self._sales = {sale.id: sale for sale in sales}
        self.sale_model.set_records(sales)
        self.sale_page_label.setText(self._page_label(snapshot.sales))
        self.sale_newer.setEnabled(snapshot.sales.has_previous)
        self.sale_older.setEnabled(snapshot.sales.has_next)
        self._clear_details()

    @staticmethod
    def _page_label(
        page: HistoryPage[Expense] | HistoryPage[SaleSummary],
    ) -> str:
        if page.total == 0:
            return "0 records"
        return (
            f"{page.offset + 1}–{page.offset + len(page.records)} "
            f"of {page.total} (newest first)"
        )

    def _change_expense_page(self, direction: int) -> None:
        offset = max(
            0, self._expense_page.offset + direction * self._expense_page.limit
        )
        self._load_page(offset, self._sale_page.offset)

    def apply_filter(self) -> None:
        self._load_page(0, 0)

    def clear_filter(self) -> None:
        if not self.search.text():
            return
        self.search.clear()
        self.apply_filter()

    def edit_selected_expense_proofs(self) -> None:
        expense = self._selected_expense(self.expense_table)
        if expense is None:
            QMessageBox.information(
                self, "Select one item", "Select exactly one purchase-history item."
            )
            return
        self._edit_expense_proofs(expense)

    def edit_selected_detail_proofs(self) -> None:
        expense = self._selected_expense(self.detail_table)
        if expense is None:
            QMessageBox.information(
                self, "Select one item", "Select exactly one sold item."
            )
            return
        self._edit_expense_proofs(expense)

    def _selected_expense(self, table: QTableView) -> Expense | None:
        ids = selected_ids(table)
        if len(ids) != 1:
            return None
        if table is self.expense_table:
            return self._expenses.get(ids[0])
        return next(
            (item for item in self._detail_page.records if item.id == ids[0]),
            None,
        )

    def _edit_expense_proofs(self, expense: Expense) -> None:
        update = ProofEditDialog.get_update(
            expense.proofs,
            lambda proof_id: self.services.proof_file(expense.id, proof_id),
            self,
        )
        if update is None:
            return
        retained_ids, new_proofs = update
        if (
            retained_ids == tuple(proof.id for proof in expense.proofs)
            and not new_proofs
        ):
            return
        self.run_command(
            lambda: self.services.replace_expense_proofs(
                expense.id, retained_ids, new_proofs
            ),
            self.data_changed.emit,
            "Unable to update proofs",
        )

    def _has_one_selected_sale(self) -> bool:
        ids = selected_ids(self.sale_table)
        return len(ids) == 1 and ids[0] in self._sales

    def _change_sale_page(self, direction: int) -> None:
        offset = max(0, self._sale_page.offset + direction * self._sale_page.limit)
        self._load_page(self._expense_page.offset, offset)

    def _load_page(self, expense_offset: int, sale_offset: int) -> None:
        self.run_operation(
            lambda: self.services.sales_snapshot(
                expense_offset,
                sale_offset,
                self._expense_page.limit,
                self.search.text(),
            ),
            self.apply_snapshot,
            "Unable to load history",
        )

    def _sale_selection_changed(self, *_: object) -> None:
        ids = selected_ids(self.sale_table)
        if len(ids) == 1 and ids[0] in self._sales:
            self._load_detail_page(ids[0], 0)
        else:
            self._clear_details()

    def _clear_details(self) -> None:
        self._detail_generation += 1
        self._detail_sale_id = None
        self._detail_page = HistoryPage((), 0, 0, self._detail_page.limit)
        self.detail_model.set_records(())
        self.detail_page_label.setText("Select one sale")
        self.detail_newer.setEnabled(False)
        self.detail_older.setEnabled(False)

    def _load_detail_page(self, sale_id: int, offset: int) -> None:
        self._detail_generation += 1
        generation = self._detail_generation
        self._detail_sale_id = sale_id
        self.detail_newer.setEnabled(False)
        self.detail_older.setEnabled(False)
        self.detail_page_label.setText("Loading...")
        self.tasks.run(
            lambda: self.services.sale_item_page(
                sale_id,
                offset,
                self._detail_page.limit,
            ),
            lambda page: self._detail_loaded(generation, sale_id, page),
            lambda error: self._detail_failed(generation, sale_id, error),
            owner=self,
        )

    def _detail_loaded(
        self,
        generation: int,
        sale_id: int,
        page: HistoryPage[Expense],
    ) -> None:
        if not self._current_detail_request(generation, sale_id):
            return
        self._detail_page = page
        self.detail_model.set_records(page.records)
        self.detail_page_label.setText(self._page_label(page))
        self.detail_newer.setEnabled(page.has_previous)
        self.detail_older.setEnabled(page.has_next)

    def _detail_failed(self, generation: int, sale_id: int, error: Exception) -> None:
        if not self._current_detail_request(generation, sale_id):
            return
        self._clear_details()
        show_error(self, "Unable to load sale items", error)

    def _current_detail_request(self, generation: int, sale_id: int) -> bool:
        return (
            self._detail_generation == generation
            and self._detail_sale_id == sale_id
            and selected_ids(self.sale_table) == [sale_id]
            and sale_id in self._sales
        )

    def _change_detail_page(self, direction: int) -> None:
        if self._detail_sale_id is None:
            return
        offset = max(0, self._detail_page.offset + direction * self._detail_page.limit)
        self._load_detail_page(self._detail_sale_id, offset)

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
        self._clear_details()
        self.run_command(
            lambda: self.services.undo_sale(sale.id),
            self.data_changed.emit,
            "Unable to undo sale",
        )

    def edit_selected_sale(self) -> None:
        ids = selected_ids(self.sale_table)
        if len(ids) != 1:
            QMessageBox.information(
                self, "Select one sale", "Select exactly one sale to edit."
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
        terms = SaleDialog.get_sale(
            sale.name,
            self,
            initial=SaleTerms(sale.selling_price_cents, sale.sale_date),
        )
        if terms is None or terms == SaleTerms(
            sale.selling_price_cents, sale.sale_date
        ):
            return
        self._clear_details()
        self.run_command(
            lambda: self.services.update_sale(sale.id, terms),
            self.data_changed.emit,
            "Unable to edit sale",
        )
