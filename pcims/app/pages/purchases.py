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
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from pcims.app.async_page import AsyncCommandPage
from pcims.app.common import show_error
from pcims.app.dialogs import ProofEditDialog
from pcims.app.formatting import (
    allocate_cents,
    format_cents,
    parse_money_cents,
)
from pcims.app.table_model import (
    Column,
    ContextAction,
    RecordTableModel,
    configure_context_menu,
    configure_table_view,
    selected_ids,
)
from pcims.app.tasks import TaskManager
from pcims.contracts import PurchaseOperations, PurchasesSnapshot
from pcims.domain import ITEM_CONDITIONS, ITEM_TYPES, ItemDetails, ItemType, NewExpense
from pcims.drafts import DraftPurchase, PurchaseDraftStore
from pcims.proofs import NewProof

StagedPurchase = DraftPurchase


class PurchasesPage(AsyncCommandPage):
    data_changed = Signal()

    def __init__(
        self,
        services: PurchaseOperations,
        *,
        tasks: TaskManager,
        draft_store: PurchaseDraftStore | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(tasks, parent)
        self.services = services
        self._draft_store = draft_store
        self._draft_load_error: Exception | None = None
        self._staged: list[DraftPurchase] = []
        if self._draft_store is not None:
            try:
                self._staged = list(self._draft_store.load())
            except (OSError, TypeError, ValueError) as error:
                self._draft_load_error = error
        self._next_staged_id = 1
        if self._staged:
            self._next_staged_id = max(item.staged_id for item in self._staged) + 1
        self._pending_proofs: tuple[NewProof, ...] = ()

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
        self.vendor = QLineEdit()
        self.serial_number = QLineEdit()
        self.storage_location = QLineEdit()
        self.condition = QComboBox()
        self.condition.addItem("Not specified", None)
        for condition in ITEM_CONDITIONS:
            self.condition.addItem(condition, condition)
        self.has_warranty = QCheckBox("Warranty end date")
        self.warranty_until = QDateEdit(QDate.currentDate())
        self.warranty_until.setCalendarPopup(True)
        self.warranty_until.setDisplayFormat("yyyy-MM-dd")
        self.warranty_until.setEnabled(False)
        self.has_warranty.toggled.connect(self.warranty_until.setEnabled)
        warranty_row = QHBoxLayout()
        warranty_row.addWidget(self.has_warranty)
        warranty_row.addWidget(self.warranty_until)
        warranty_widget = QWidget()
        warranty_widget.setLayout(warranty_row)
        self.notes = QPlainTextEdit()
        self.notes.setMaximumHeight(80)
        self.proof_label = QLabel("No proofs selected")
        proof_button = QPushButton("Choose proofs…")
        proof_button.clicked.connect(self.choose_proofs)
        proof_row = QHBoxLayout()
        proof_row.addWidget(proof_button)
        proof_row.addWidget(self.proof_label, 1)
        proof_widget = QWidget()
        proof_widget.setLayout(proof_row)

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
        form.addRow("Vendor", self.vendor)
        form.addRow("Serial number", self.serial_number)
        form.addRow("Storage location", self.storage_location)
        form.addRow("Condition", self.condition)
        form.addRow("Warranty", warranty_widget)
        form.addRow("Notes", self.notes)
        form.addRow("Proofs of purchase", proof_widget)
        add_button = QPushButton("Add to purchase")
        add_button.clicked.connect(self.add_line)
        form.addRow("", add_button)
        form_box = QGroupBox("New purchase line")
        form_box.setLayout(form)

        self.table_model = RecordTableModel[DraftPurchase](
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
                Column(
                    "Proofs",
                    lambda item: str(len(item.proofs)),
                    lambda item: len(item.proofs),
                ),
            ),
            lambda item: item.staged_id,
        )
        self.table = QTableView()
        configure_table_view(self.table, self.table_model)
        configure_context_menu(
            self.table,
            (
                ContextAction(
                    "Remove selected",
                    self.remove_selected,
                    lambda: bool(selected_ids(self.table)),
                ),
            ),
        )
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
        if self._draft_load_error is not None:
            error = self._draft_load_error
            self._draft_load_error = None
            show_error(self, "Unable to restore purchase draft", error)

    @property
    def has_staged_items(self) -> bool:
        return bool(self._staged or self._pending_proofs)

    def discard_staged(self) -> None:
        """Discard purchase lines that have not been written to the database."""
        self._staged.clear()
        self._pending_proofs = ()
        self._render_pending_proofs()
        self._render_staged()

    def choose_proofs(self) -> None:
        proofs = ProofEditDialog.get_new_proofs(self._pending_proofs, self)
        if proofs is None:
            return
        self._pending_proofs = proofs
        self._render_pending_proofs()

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
        try:
            details = ItemDetails(
                vendor=self.vendor.text(),
                serial_number=self.serial_number.text(),
                storage_location=self.storage_location.text(),
                condition=self.condition.currentData(),
                warranty_until=(
                    cast(date, self.warranty_until.date().toPython())
                    if self.has_warranty.isChecked()
                    else None
                ),
                notes=self.notes.toPlainText(),
            )
        except (TypeError, ValueError) as error:
            show_error(self, "Invalid item details", error)
            return
        for price_cents in prices:
            self._staged.append(
                DraftPurchase(
                    self._next_staged_id,
                    NewExpense(
                        name,
                        cast(ItemType, self.type.currentText()),
                        price_cents,
                        purchase_date,
                        details,
                    ),
                    self._pending_proofs,
                )
            )
            self._next_staged_id += 1
        self.name.clear()
        self.price.clear()
        self.quantity.setValue(1)
        self.vendor.clear()
        self.serial_number.clear()
        self.storage_location.clear()
        self.condition.setCurrentIndex(0)
        self.has_warranty.setChecked(False)
        self.notes.clear()
        self._pending_proofs = ()
        self._render_pending_proofs()
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
        proofs = [item.proofs for item in self._staged]
        count = len(items)
        operation = (
            (lambda: self.services.add_expenses(items, proofs))
            if any(proofs)
            else (lambda: self.services.add_expenses(items))
        )
        self.run_command(
            operation,
            lambda: self._purchase_recorded(count),
            "Unable to record purchase",
        )

    def _purchase_recorded(self, count: int) -> None:
        self._staged.clear()
        self._render_staged()
        self.data_changed.emit()
        QMessageBox.information(self, "Purchase recorded", f"Recorded {count} item(s).")

    def _render_staged(self) -> None:
        if self._draft_store is not None:
            try:
                self._draft_store.save(tuple(self._staged))
            except OSError as error:
                show_error(self, "Unable to save purchase draft", error)
        self.table_model.set_records(self._staged)
        self.total_label.setText(
            "Staged total: "
            f"{format_cents(sum(item.expense.price_cents for item in self._staged))}"
        )
        self.commit_button.setEnabled(bool(self._staged))

    def _render_pending_proofs(self) -> None:
        count = len(self._pending_proofs)
        self.proof_label.setText(
            "No proofs selected" if count == 0 else f"{count} proof(s) selected"
        )
