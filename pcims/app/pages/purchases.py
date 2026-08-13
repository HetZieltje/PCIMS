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
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from pcims.app.common import (
    DATA_OPERATION_ERRORS,
    configure_table,
    selected_ids,
    show_error,
    table_item,
)
from pcims.app.formatting import (
    allocate_cents,
    cents_as_decimal,
    format_cents,
    parse_money_cents,
)
from pcims.db.queries import add_expenses, list_expenses
from pcims.domain import ITEM_TYPES


class PurchasesPage(QWidget):
    data_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._staged = []
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

        self.table = QTableWidget()
        configure_table(self.table, ("Line", "Name", "Type", "Cost", "Purchase date"))
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
        self.refresh()

    def refresh(self):
        names = sorted({expense.name for expense in list_expenses()}, key=str.casefold)
        self._completion_model.setStringList(names)

    @property
    def has_staged_items(self):
        return bool(self._staged)

    def discard_staged(self):
        """Discard purchase lines that have not been written to the database."""
        self._staged.clear()
        self._render_staged()

    def add_line(self):
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
        purchase_date = self.purchase_date.date().toPython()
        for price_cents in prices:
            self._staged.append(
                {
                    "staged_id": self._next_staged_id,
                    "name": name,
                    "item_type": self.type.currentText(),
                    "price_cents": price_cents,
                    "purchase_date": purchase_date,
                }
            )
            self._next_staged_id += 1
        self.name.clear()
        self.price.clear()
        self.quantity.setValue(1)
        self.name.setFocus()
        self._render_staged()

    def remove_selected(self):
        ids = set(selected_ids(self.table))
        self._staged = [item for item in self._staged if item["staged_id"] not in ids]
        self._render_staged()

    def commit_purchase(self):
        if not self._staged:
            QMessageBox.information(
                self, "Nothing to record", "Add at least one item first."
            )
            return
        items = [
            {
                "name": item["name"],
                "item_type": item["item_type"],
                "price": cents_as_decimal(item["price_cents"]),
                "purchase_date": item["purchase_date"],
            }
            for item in self._staged
        ]
        try:
            add_expenses(items)
        except DATA_OPERATION_ERRORS as error:
            show_error(self, "Unable to record purchase", error)
            return
        count = len(items)
        self._staged.clear()
        self._render_staged()
        self.data_changed.emit()
        QMessageBox.information(self, "Purchase recorded", f"Recorded {count} item(s).")

    def _render_staged(self):
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self._staged))
        for row, item in enumerate(self._staged):
            self.table.setItem(
                row,
                0,
                table_item(item["staged_id"], item["staged_id"], item["staged_id"]),
            )
            self.table.setItem(row, 1, table_item(item["name"]))
            self.table.setItem(row, 2, table_item(item["item_type"]))
            self.table.setItem(
                row,
                3,
                table_item(
                    format_cents(item["price_cents"]), sort_value=item["price_cents"]
                ),
            )
            self.table.setItem(
                row,
                4,
                table_item(
                    item["purchase_date"].isoformat(),
                    sort_value=item["purchase_date"].toordinal(),
                ),
            )
        self.table.setSortingEnabled(True)
        self.total_label.setText(
            f"Staged total: {format_cents(sum(item['price_cents'] for item in self._staged))}"
        )
        self.commit_button.setEnabled(bool(self._staged))
