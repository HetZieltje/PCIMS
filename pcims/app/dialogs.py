"""Reusable Qt dialogs for PCIMS workflows."""

from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import cast

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from pcims.app.assembly_model import AssemblyTreeModel
from pcims.app.formatting import parse_money_cents
from pcims.domain import (
    ITEM_CONDITIONS,
    ITEM_TYPES,
    ItemDetails,
    ItemType,
    NewExpense,
    SaleTerms,
    normalized_text,
)
from pcims.models import AssembledPC, Expense
from pcims.proofs import PROOF_FILE_FILTER, NewProof, ProofSummary


class ProofEditDialog(QDialog):
    """Manage persisted and newly selected proofs without loading all blobs."""

    def __init__(
        self,
        proofs: tuple[ProofSummary, ...] = (),
        new_proofs: tuple[NewProof, ...] = (),
        proof_loader: Callable[[int], NewProof] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Proofs of purchase")
        self.setModal(True)
        self.resize(620, 420)
        self._proof_loader = proof_loader
        self._retained_ids: tuple[int, ...] = ()
        self._new_proofs: tuple[NewProof, ...] = ()
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        for summary in proofs:
            self._append(summary)
        for new_proof in new_proofs:
            self._append(new_proof)

        add_button = QPushButton("Add files…")
        add_button.clicked.connect(self._add_files)
        remove_button = QPushButton("Remove selected")
        remove_button.clicked.connect(self._remove_selected)
        save_button = QPushButton("Save a copy…")
        save_button.clicked.connect(self._save_copy)
        actions = QHBoxLayout()
        actions.addWidget(add_button)
        actions.addWidget(remove_button)
        actions.addWidget(save_button)
        actions.addStretch()
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_changes)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel("Attach PDF or image receipts. Each file may be up to 20 MiB.")
        )
        layout.addWidget(self.list)
        layout.addLayout(actions)
        layout.addWidget(buttons)

    def _append(self, proof: ProofSummary | NewProof) -> None:
        size = (
            proof.size_bytes if isinstance(proof, ProofSummary) else len(proof.content)
        )
        item = QListWidgetItem(f"{proof.file_name}  ({size / 1024:.1f} KiB)")
        item.setData(Qt.ItemDataRole.UserRole, proof)
        self.list.addItem(item)

    def _entries(self) -> tuple[ProofSummary | NewProof, ...]:
        return tuple(
            self.list.item(row).data(Qt.ItemDataRole.UserRole)
            for row in range(self.list.count())
        )

    def _add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Select proofs of purchase", "", PROOF_FILE_FILTER
        )
        existing_names = {proof.file_name.casefold() for proof in self._entries()}
        errors: list[str] = []
        for path in paths:
            try:
                proof = NewProof.from_path(path)
                if proof.file_name.casefold() in existing_names:
                    raise ValueError(f"{proof.file_name} is already attached.")
                self._append(proof)
                existing_names.add(proof.file_name.casefold())
            except (OSError, TypeError, ValueError) as error:
                errors.append(str(error))
        if errors:
            QMessageBox.warning(self, "Some proofs were not added", "\n".join(errors))

    def _remove_selected(self) -> None:
        for item in self.list.selectedItems():
            self.list.takeItem(self.list.row(item))

    def _selected_proof(self) -> ProofSummary | NewProof | None:
        selected = self.list.selectedItems()
        if len(selected) != 1:
            QMessageBox.information(
                self, "Select one proof", "Select exactly one proof to save."
            )
            return None
        return cast(
            ProofSummary | NewProof,
            selected[0].data(Qt.ItemDataRole.UserRole),
        )

    def _save_copy(self) -> None:
        selected = self._selected_proof()
        if selected is None:
            return
        if isinstance(selected, ProofSummary):
            if self._proof_loader is None:
                QMessageBox.warning(
                    self, "Proof unavailable", "This proof cannot be loaded here."
                )
                return
            try:
                proof = self._proof_loader(selected.id)
            except Exception as error:  # noqa: BLE001 - user-triggered file boundary
                QMessageBox.warning(self, "Unable to load proof", str(error))
                return
        else:
            proof = selected
        destination, _ = QFileDialog.getSaveFileName(
            self, "Save proof copy", proof.file_name, "All files (*)"
        )
        if not destination:
            return
        try:
            Path(destination).write_bytes(proof.content)
        except OSError as error:
            QMessageBox.warning(self, "Unable to save proof", str(error))

    def _accept_changes(self) -> None:
        entries = self._entries()
        self._retained_ids = tuple(
            proof.id for proof in entries if isinstance(proof, ProofSummary)
        )
        self._new_proofs = tuple(
            proof for proof in entries if isinstance(proof, NewProof)
        )
        self.accept()

    @classmethod
    def get_update(
        cls,
        proofs: tuple[ProofSummary, ...],
        proof_loader: Callable[[int], NewProof],
        parent: QWidget | None = None,
    ) -> tuple[tuple[int, ...], tuple[NewProof, ...]] | None:
        dialog = cls(proofs, proof_loader=proof_loader, parent=parent)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog._retained_ids, dialog._new_proofs

    @classmethod
    def get_new_proofs(
        cls,
        proofs: tuple[NewProof, ...] = (),
        parent: QWidget | None = None,
    ) -> tuple[NewProof, ...] | None:
        dialog = cls(new_proofs=proofs, parent=parent)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog._new_proofs


class ExpenseEditDialog(QDialog):
    def __init__(
        self,
        expense: Expense,
        parent: QWidget | None = None,
        *,
        title: str = "Edit component",
        name_label: str = "Item name",
        price_label: str | None = None,
        show_type: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.name = QLineEdit(expense.name)
        self.item_type = QComboBox()
        self.item_type.addItems(ITEM_TYPES)
        self.item_type.setCurrentText(expense.item_type)
        self.amount = QLineEdit(
            f"{expense.price_cents // 100}.{expense.price_cents % 100:02d}"
        )
        self.purchase_date = QDateEdit(
            QDate(
                expense.purchase_date.year,
                expense.purchase_date.month,
                expense.purchase_date.day,
            )
        )
        self.purchase_date.setCalendarPopup(True)
        self.purchase_date.setDisplayFormat("yyyy-MM-dd")
        self.vendor = QLineEdit(expense.details.vendor)
        self.serial_number = QLineEdit(expense.details.serial_number)
        self.storage_location = QLineEdit(expense.details.storage_location)
        self.condition = QComboBox()
        self.condition.addItem("Not specified", None)
        for condition in ITEM_CONDITIONS:
            self.condition.addItem(condition, condition)
        if expense.details.condition is not None:
            self.condition.setCurrentText(expense.details.condition)
        self.has_warranty = QCheckBox("Warranty end date")
        warranty = expense.details.warranty_until
        warranty_qdate = (
            QDate(warranty.year, warranty.month, warranty.day)
            if warranty is not None
            else QDate.currentDate()
        )
        self.warranty_until = QDateEdit(warranty_qdate)
        self.warranty_until.setCalendarPopup(True)
        self.warranty_until.setDisplayFormat("yyyy-MM-dd")
        self.has_warranty.setChecked(expense.details.warranty_until is not None)
        self.warranty_until.setEnabled(self.has_warranty.isChecked())
        self.has_warranty.toggled.connect(self.warranty_until.setEnabled)
        warranty_row = QHBoxLayout()
        warranty_row.addWidget(self.has_warranty)
        warranty_row.addWidget(self.warranty_until)
        warranty_widget = QWidget()
        warranty_widget.setLayout(warranty_row)
        self.notes = QPlainTextEdit(expense.details.notes)
        self.notes.setMaximumHeight(100)
        self.error_label = QLabel()
        self.error_label.setStyleSheet("color: #c62828")
        self._replacement: NewExpense | None = None

        form = QFormLayout()
        form.addRow(name_label, self.name)
        if show_type:
            form.addRow("Component type", self.item_type)
        form.addRow(
            price_label
            or (
                "Allocated value"
                if expense.cost_origin == "extracted"
                else "Purchase price"
            ),
            self.amount,
        )
        form.addRow("Purchase date", self.purchase_date)
        form.addRow("Vendor", self.vendor)
        form.addRow("Serial number", self.serial_number)
        form.addRow("Storage location", self.storage_location)
        form.addRow("Condition", self.condition)
        form.addRow("Warranty", warranty_widget)
        form.addRow("Notes", self.notes)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.error_label)
        layout.addWidget(buttons)
        self.name.selectAll()
        self.name.setFocus()

    def _validate(self) -> None:
        try:
            self._replacement = NewExpense(
                self.name.text(),
                cast(ItemType, self.item_type.currentText()),
                parse_money_cents(self.amount.text(), "Purchase price"),
                cast(date, self.purchase_date.date().toPython()),
                ItemDetails(
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
                ),
            )
        except (TypeError, ValueError) as error:
            self.error_label.setText(str(error))
            return
        self.accept()

    @classmethod
    def get_expense(
        cls, expense: Expense, parent: QWidget | None = None
    ) -> NewExpense | None:
        dialog = cls(expense, parent)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog._replacement


class LaptopEditDialog(ExpenseEditDialog):
    def __init__(
        self, expense: Expense | None = None, parent: QWidget | None = None
    ) -> None:
        if expense is None:
            today = datetime.now(UTC).astimezone().date()
            expense = Expense(0, "", "Extra", 0, today, cash_paid_cents=0)
        super().__init__(
            expense,
            parent,
            title="Edit laptop" if expense.id else "Add laptop",
            name_label="Model number",
            price_label="Purchase price",
            show_type=False,
        )
        self.item_type.setCurrentText("Extra")

    @classmethod
    def get_laptop(
        cls, expense: Expense | None = None, parent: QWidget | None = None
    ) -> NewExpense | None:
        dialog = cls(expense, parent)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog._replacement


class LaptopExtractionDialog(QDialog):
    def __init__(
        self,
        available: tuple[Expense, ...],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Remove or replace factory component")
        self.setModal(True)
        self._available = available
        self._result: tuple[str, int, NewExpense, int | None] | None = None
        self.component_type = QComboBox()
        self.component_type.addItems(("RAM", "SSD", "HDD"))
        self.slot_number = QSpinBox()
        self.slot_number.setRange(1, 32)
        self.removed_name = QLineEdit()
        self.removed_name.setPlaceholderText("e.g. Factory 16 GB module")
        self.removed_value = QLineEdit()
        self.removed_value.setPlaceholderText("0.00")
        self.replacement = QComboBox()
        self.error_label = QLabel()
        self.error_label.setStyleSheet("color: #c62828")
        self.component_type.currentTextChanged.connect(self._fill_replacements)
        self._fill_replacements()
        form = QFormLayout()
        form.addRow("Component type", self.component_type)
        form.addRow("Slot number", self.slot_number)
        form.addRow("Removed factory part", self.removed_name)
        form.addRow("Value transferred from laptop", self.removed_value)
        form.addRow("Install replacement", self.replacement)
        note = QLabel(
            "Only explicitly removed RAM or storage is indexed. The entered value is "
            "subtracted from the laptop and assigned to the removed component."
        )
        note.setWordWrap(True)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(note)
        layout.addLayout(form)
        layout.addWidget(self.error_label)
        layout.addWidget(buttons)

    def _fill_replacements(self) -> None:
        selected = self.replacement.currentData()
        self.replacement.clear()
        self.replacement.addItem("Leave slot empty", None)
        for item in self._available:
            if item.item_type == self.component_type.currentText():
                self.replacement.addItem(
                    f"#{item.id} {item.name} ({item.price_cents / 100:.2f})", item.id
                )
        index = self.replacement.findData(selected)
        if index >= 0:
            self.replacement.setCurrentIndex(index)

    def _validate(self) -> None:
        try:
            kind = cast(ItemType, self.component_type.currentText())
            value = parse_money_cents(self.removed_value.text(), "Removed part value")
            if value <= 0:
                raise ValueError("Removed part value must be above zero.")
            extracted = NewExpense(
                self.removed_name.text(),
                kind,
                value,
                datetime.now(UTC).astimezone().date(),
                ItemDetails(),
            )
        except (TypeError, ValueError) as error:
            self.error_label.setText(str(error))
            return
        self._result = (
            self.component_type.currentText(),
            self.slot_number.value(),
            extracted,
            self.replacement.currentData(),
        )
        self.accept()

    @classmethod
    def get_extraction(
        cls, available: tuple[Expense, ...], parent: QWidget | None = None
    ) -> tuple[str, int, NewExpense, int | None] | None:
        dialog = cls(available, parent)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog._result


class LaptopReplacementDialog(QDialog):
    def __init__(
        self,
        component_type: str,
        current_item_id: int | None,
        available: tuple[Expense, ...],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Change laptop replacement")
        self.replacement = QComboBox()
        self.replacement.addItem("Leave slot empty", None)
        for item in available:
            if item.item_type == component_type or item.id == current_item_id:
                self.replacement.addItem(
                    f"#{item.id} {item.name} ({item.price_cents / 100:.2f})", item.id
                )
        index = self.replacement.findData(current_item_id)
        self.replacement.setCurrentIndex(max(0, index))
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form = QFormLayout(self)
        form.addRow("Replacement", self.replacement)
        form.addRow(buttons)

    @classmethod
    def get_replacement(
        cls,
        component_type: str,
        current_item_id: int | None,
        available: tuple[Expense, ...],
        parent: QWidget | None = None,
    ) -> tuple[bool, int | None]:
        dialog = cls(component_type, current_item_id, available, parent)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False, current_item_id
        return True, dialog.replacement.currentData()


class PCEditDialog(QDialog):
    def __init__(
        self,
        pc: AssembledPC,
        candidates: tuple[Expense, ...],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit assembled PC")
        self.setModal(True)
        self.resize(760, 560)
        self.name = QLineEdit(pc.name)
        self.tree_model = AssemblyTreeModel()
        self.tree_model.set_records(candidates)
        self.tree_model.set_checked_ids(tuple(part.id for part in pc.parts))
        self.tree = QTreeView()
        self.tree.setModel(self.tree_model)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self.tree.header().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self.tree.expandAll()
        self.error_label = QLabel()
        self.error_label.setStyleSheet("color: #c62828")
        self._result: tuple[str, tuple[int, ...]] | None = None

        form = QFormLayout()
        form.addRow("PC name", self.name)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(QLabel("Components"))
        layout.addWidget(self.tree)
        layout.addWidget(self.error_label)
        layout.addWidget(buttons)

    def _validate(self) -> None:
        try:
            name = normalized_text(self.name.text(), "PC name")
        except ValueError as error:
            self.error_label.setText(str(error))
            return
        expense_ids = self.tree_model.checked_ids
        if not expense_ids:
            self.error_label.setText("Select at least one component.")
            return
        self._result = name, expense_ids
        self.accept()

    @classmethod
    def get_pc(
        cls,
        pc: AssembledPC,
        candidates: tuple[Expense, ...],
        parent: QWidget | None = None,
    ) -> tuple[str, tuple[int, ...]] | None:
        dialog = cls(pc, candidates, parent)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog._result


class SaleDialog(QDialog):
    def __init__(
        self,
        item_name: str,
        parent: QWidget | None = None,
        *,
        initial: SaleTerms | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit sale" if initial is not None else "Record sale")
        self.setModal(True)
        self.amount = QLineEdit()
        self.amount.setPlaceholderText("0.00")
        initial_date = (
            QDate(
                initial.sale_date.year, initial.sale_date.month, initial.sale_date.day
            )
            if initial is not None
            else QDate.currentDate()
        )
        self.sale_date = QDateEdit(initial_date)
        self.sale_date.setCalendarPopup(True)
        self.sale_date.setDisplayFormat("yyyy-MM-dd")
        self.error_label = QLabel()
        self._amount_cents = 0
        self.error_label.setStyleSheet("color: #c62828")

        form = QFormLayout()
        form.addRow("Item", QLabel(item_name))
        form.addRow("Total selling price", self.amount)
        form.addRow("Sale date", self.sale_date)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.error_label)
        layout.addWidget(buttons)
        if initial is not None:
            cents = initial.selling_price_cents
            self.amount.setText(f"{cents // 100}.{cents % 100:02d}")
            self.amount.selectAll()
        self.amount.setFocus()

    def _validate(self) -> None:
        try:
            self._amount_cents = parse_money_cents(self.amount.text())
        except ValueError as error:
            self.error_label.setText(str(error))
            return
        self.accept()

    @classmethod
    def get_sale(
        cls,
        item_name: str,
        parent: QWidget | None = None,
        *,
        initial: SaleTerms | None = None,
    ) -> SaleTerms | None:
        dialog = cls(item_name, parent, initial=initial)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return SaleTerms(
            dialog._amount_cents,
            cast(date, dialog.sale_date.date().toPython()),
        )
