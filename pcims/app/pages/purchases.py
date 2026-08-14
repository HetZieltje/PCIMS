from dataclasses import dataclass
from datetime import date
from typing import cast

from PySide6.QtCore import QDate, QStringListModel, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QCompleter,
    QDateEdit,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from pcims.app.async_page import AsyncCommandPage
from pcims.app.common import show_error
from pcims.app.formatting import (
    allocate_cents,
    format_cents,
    parse_money_cents,
)
from pcims.app.table_model import (
    Column,
    RecordTableModel,
    configure_table_view,
    selected_ids,
)
from pcims.app.tasks import TaskManager
from pcims.domain import ITEM_TYPES, ItemType, NewExpense
from pcims.services import ApplicationServices, PurchasesSnapshot


@dataclass(frozen=True, slots=True)
class StagedPurchase:
    staged_id: int
    expense: NewExpense


class PurchasesPage(AsyncCommandPage):
    data_changed = Signal()

    def __init__(
        self,
        services: ApplicationServices,
        parent: QWidget | None = None,
        tasks: TaskManager | None = None,
    ) -> None:
        super().__init__(parent, tasks)
        self.services = services
        self._staged: list[StagedPurchase] = []
        self._next_staged_id = 1

        self.name = QLineEdit()
        self.type = QComboBox()
        self.type.addItems(ITEM_TYPES)
        self.quantity = QSpinBox()
        self.quantity.setRange(1, 999)
        self.price = QLineEdit()
        self.price.setPlaceholderText("0.00")
        self.total_for_quantity = QCheckBox(
            "Entered amount is the total for this quantity"
        )
        self.purchase_date = QDateEdit(QDate.currentDate())
        self.purchase_date.setCalendarPopup(True)
        self.purchase_date.setDisplayFormat("yyyy-MM-dd")

        self._completion_model = QStringListModel(self)
        completer = QCompleter(self._completion_model, self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.name.setCompleter(completer)

        form = QFormLayout()
        form.addRow("Item name", self.name)
        form.addRow("Component type", self.type)
        form.addRow("Quantity", self.quantity)
        form.addRow("Price", self.price)
        form.addRow("", self.total_for_quantity)
        form.addRow("Purchase date", self.purchase_date)
        add_button = QPushButton("Add to purchase")
        add_button.clicked.connect(self.add_line)
        form.addRow("", add_button)
        form_box = QGroupBox("New purchase line")
        form_box.setLayout(form)

        self.table_model = RecordTableModel[StagedPurchase](
            (
                Column(
                    "Line",
                    lambda item: str(item.staged_id),
                    lambda item: item.staged_id,
                ),
                Column(
                    "Name",
                    lambda item: item.expense.name,
                    lambda item: item.expense.name.casefold(),
                ),
                Column(
                    "Type",
                    lambda item: item.expense.item_type,
                    lambda item: item.expense.item_type,
                ),
                Column(
                    "Cost",
                    lambda item: format_cents(item.expense.price_cents),
                    lambda item: item.expense.price_cents,
                ),
                Column(
                    "Purchase date",
                    lambda item: item.expense.purchase_date.isoformat(),
                    lambda item: item.expense.purchase_date.toordinal(),
                ),
            ),
            lambda item: item.staged_id,
        )
        self.table = QTableView()
        configure_table_view(self.table, self.table_model)
        self.table.setColumnHidden(0, True)
        remove_button = QPushButton("Remove selected")
        remove_button.clicked.connect(self.remove_selected)
        self.commit_button = QPushButton("Record purchase")
        self.commit_button.setDefault(True)
        self.commit_button.clicked.connect(self.commit_purchase)
        self.total_label = QLabel("Staged total: €0.00")

        actions = QHBoxLayout()
        actions.addWidget(remove_button)
        actions.addStretch()
        actions.addWidget(self.total_label)
        actions.addWidget(self.commit_button)
        right = QVBoxLayout()
        right.addWidget(self.table)
        right.addLayout(actions)
        layout = QHBoxLayout(self)
        layout.addWidget(form_box, 0)
        layout.addLayout(right, 1)
        self._render_staged()

    def refresh(self) -> None:
        self.apply_snapshot(self.load_snapshot())

    def load_snapshot(self) -> PurchasesSnapshot:
        return self.services.purchases_snapshot()

    def apply_snapshot(self, snapshot: PurchasesSnapshot) -> None:
        self._completion_model.setStringList(list(snapshot.expense_names))

    @property
    def has_staged_items(self) -> bool:
        return bool(self._staged)

    def discard_staged(self) -> None:
        """Discard purchase lines that have not been written to the database."""
        self._staged.clear()
        self._render_staged()

    def add_line(self) -> None:
        name = self.name.text().strip()
        if not name:
            QMessageBox.warning(self, "Missing name", "Enter an item name.")
            return
        try:
            entered_cents = parse_money_cents(self.price.text())
        except ValueError as error:
            show_error(self, "Invalid price", error)
            return
        quantity = self.quantity.value()
        prices = (
            allocate_cents(entered_cents, quantity)
            if self.total_for_quantity.isChecked()
            else [entered_cents] * quantity
        )
        purchase_date = cast(date, self.purchase_date.date().toPython())
        for price_cents in prices:
            self._staged.append(
                StagedPurchase(
                    self._next_staged_id,
                    NewExpense(
                        name,
                        cast(ItemType, self.type.currentText()),
                        price_cents,
                        purchase_date,
                    ),
                )
            )
            self._next_staged_id += 1
        self.name.clear()
        self.price.clear()
        self.quantity.setValue(1)
        self.name.setFocus()
        self._render_staged()

    def remove_selected(self) -> None:
        ids = set(selected_ids(self.table))
        self._staged = [item for item in self._staged if item.staged_id not in ids]
        self._render_staged()

    def commit_purchase(self) -> None:
        if not self._staged:
            QMessageBox.information(
                self, "Nothing to record", "Add at least one item first."
            )
            return
        items = [item.expense for item in self._staged]
        count = len(items)
        self.run_command(
            lambda: self.services.add_expenses(items),
            lambda: self._purchase_recorded(count),
            "Unable to record purchase",
        )

    def _purchase_recorded(self, count: int) -> None:
        self._staged.clear()
        self._render_staged()
        self.data_changed.emit()
        QMessageBox.information(self, "Purchase recorded", f"Recorded {count} item(s).")

    def _render_staged(self) -> None:
        self.table_model.set_records(self._staged)
        self.total_label.setText(
            "Staged total: "
            f"{format_cents(sum(item.expense.price_cents for item in self._staged))}"
        )
        self.commit_button.setEnabled(bool(self._staged))
