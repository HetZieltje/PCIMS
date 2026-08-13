"""Reusable Qt dialogs for PCIMS workflows."""

from datetime import date
from decimal import Decimal
from typing import cast

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from pcims.app.formatting import cents_as_decimal, parse_money_cents


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
    ) -> tuple[Decimal, date] | None:
        dialog = cls(item_name, parent)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return cents_as_decimal(dialog._amount_cents), cast(
            date, dialog.sale_date.date().toPython()
        )
