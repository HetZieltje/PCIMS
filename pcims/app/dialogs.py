"""Reusable Qt dialogs for PCIMS workflows."""

from datetime import date
from typing import cast

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from pcims.app.assembly_model import AssemblyTreeModel
from pcims.app.formatting import parse_money_cents
from pcims.domain import ITEM_TYPES, ItemType, NewExpense, SaleTerms, normalized_text
from pcims.models import AssembledPC, Expense


class ExpenseEditDialog(QDialog):
    def __init__(self, expense: Expense, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit component")
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
        self.error_label = QLabel()
        self.error_label.setStyleSheet("color: #c62828")
        self._replacement: NewExpense | None = None

        form = QFormLayout()
        form.addRow("Item name", self.name)
        form.addRow("Component type", self.item_type)
        form.addRow("Purchase price", self.amount)
        form.addRow("Purchase date", self.purchase_date)
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
    def __init__(self, item_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Record sale")
        self.setModal(True)
        self.amount = QLineEdit()
        self.amount.setPlaceholderText("0.00")
        self.sale_date = QDateEdit(QDate.currentDate())
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
        cls, item_name: str, parent: QWidget | None = None
    ) -> SaleTerms | None:
        dialog = cls(item_name, parent)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return SaleTerms(
            dialog._amount_cents,
            cast(date, dialog.sale_date.date().toPython()),
        )
