from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
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
from pcims.app.dialogs import SaleDialog
from pcims.app.formatting import format_cents
from pcims.db.queries import (
    delete_expenses,
    disassemble_pc,
    list_inventory,
    list_pcs,
    rename_expenses,
    rename_pc,
    sell_items,
    sell_pc,
)
from pcims.domain import ITEM_TYPES


class InventoryPage(QWidget):
    data_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._all_parts = ()
        self._parts = {}
        self._pcs = {}
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter by name…")
        self.type_filter = QComboBox()
        self.type_filter.addItem("All types", None)
        for item_type in ITEM_TYPES:
            self.type_filter.addItem(item_type, item_type)
        self.status_filter = QComboBox()
        self.status_filter.addItem("All unsold", "all")
        self.status_filter.addItem("Available only", "available")
        self.status_filter.addItem("Assigned to PC", "assigned")
        self.search.textChanged.connect(self._apply_filters)
        self.type_filter.currentIndexChanged.connect(self._apply_filters)
        self.status_filter.currentIndexChanged.connect(self._apply_filters)

        filters = QHBoxLayout()
        filters.addWidget(QLabel("Search"))
        filters.addWidget(self.search, 1)
        filters.addWidget(QLabel("Type"))
        filters.addWidget(self.type_filter)
        filters.addWidget(QLabel("Status"))
        filters.addWidget(self.status_filter)

        self.parts_table = QTableWidget()
        configure_table(
            self.parts_table,
            ("ID", "Name", "Type", "Cost", "Purchased", "Status"),
        )
        part_buttons = QHBoxLayout()
        for text, callback in (
            ("Sell selected", self.sell_selected_parts),
            ("Rename", self.rename_selected_parts),
            ("Delete", self.delete_selected_parts),
        ):
            button = QPushButton(text)
            button.clicked.connect(callback)
            part_buttons.addWidget(button)
        part_buttons.addStretch()
        parts_layout = QVBoxLayout()
        parts_layout.addWidget(self.parts_table)
        parts_layout.addLayout(part_buttons)
        parts_box = QGroupBox("Components and extras")
        parts_box.setLayout(parts_layout)

        self.pc_table = QTableWidget()
        configure_table(self.pc_table, ("ID", "Name", "Cost", "Components"))
        pc_buttons = QHBoxLayout()
        for text, callback in (
            ("Sell PC", self.sell_selected_pc),
            ("Rename", self.rename_selected_pc),
            ("Disassemble", self.disassemble_selected_pc),
        ):
            button = QPushButton(text)
            button.clicked.connect(callback)
            pc_buttons.addWidget(button)
        pc_buttons.addStretch()
        pc_layout = QVBoxLayout()
        pc_layout.addWidget(self.pc_table)
        pc_layout.addLayout(pc_buttons)
        pc_box = QGroupBox("Assembled PCs")
        pc_box.setLayout(pc_layout)

        self.splitter = QSplitter()
        self.splitter.addWidget(parts_box)
        self.splitter.addWidget(pc_box)
        self.splitter.setSizes((650, 550))
        layout = QVBoxLayout(self)
        layout.addLayout(filters)
        layout.addWidget(self.splitter)
        self.refresh()

    def refresh(self):
        self._all_parts = list_inventory()
        self._render_parts()
        self._render_pcs(list_pcs())

    def _apply_filters(self, *_):
        self._render_parts()

    def _render_parts(self):
        search = self.search.text().strip().casefold()
        item_type = self.type_filter.currentData()
        status = self.status_filter.currentData()
        parts = self._all_parts
        if item_type is not None:
            parts = tuple(item for item in parts if item.item_type == item_type)
        if search:
            parts = tuple(item for item in parts if search in item.name.casefold())
        if status == "available":
            parts = tuple(item for item in parts if item.is_available)
        elif status == "assigned":
            parts = tuple(item for item in parts if item.pc_id is not None)
        self._parts = {item.id: item for item in parts}

        self.parts_table.setSortingEnabled(False)
        self.parts_table.setRowCount(len(parts))
        for row, item in enumerate(parts):
            values = (
                item.id,
                item.name,
                item.item_type,
                format_cents(item.price_cents),
                item.purchase_date.isoformat(),
                item.pc_name or "Available",
            )
            sort_values = (
                item.id,
                item.name.casefold(),
                item.item_type,
                item.price_cents,
                item.purchase_date.toordinal(),
                (item.pc_name or "Available").casefold(),
            )
            for column, value in enumerate(values):
                self.parts_table.setItem(
                    row,
                    column,
                    table_item(
                        value,
                        item.id if column == 0 else None,
                        sort_value=sort_values[column],
                    ),
                )
        self.parts_table.setSortingEnabled(True)

    def _render_pcs(self, pcs):
        self._pcs = {pc.id: pc for pc in pcs}
        self.pc_table.setSortingEnabled(False)
        self.pc_table.setRowCount(len(pcs))
        for row, pc in enumerate(pcs):
            counts = {}
            for part in pc.parts:
                counts[part.item_type] = counts.get(part.item_type, 0) + 1
            summary = ", ".join(
                f"{count}× {item_type}" if count > 1 else item_type
                for item_type, count in counts.items()
            )
            values = (pc.id, pc.name, format_cents(pc.cost_cents), summary)
            sort_values = (pc.id, pc.name.casefold(), pc.cost_cents, summary.casefold())
            for column, value in enumerate(values):
                self.pc_table.setItem(
                    row,
                    column,
                    table_item(
                        value,
                        pc.id if column == 0 else None,
                        sort_value=sort_values[column],
                    ),
                )
        self.pc_table.setSortingEnabled(True)

    def _selected_parts(self):
        return [self._parts[item_id] for item_id in selected_ids(self.parts_table)]

    def _selected_pc(self):
        ids = selected_ids(self.pc_table)
        if len(ids) != 1:
            QMessageBox.information(
                self, "Select one PC", "Select exactly one assembled PC."
            )
            return None
        return self._pcs[ids[0]]

    def sell_selected_parts(self):
        parts = self._selected_parts()
        if not parts:
            QMessageBox.information(
                self, "Nothing selected", "Select one or more items to sell."
            )
            return
        if any(not part.is_available for part in parts):
            QMessageBox.warning(
                self,
                "Assigned component",
                "Disassemble the PC before selling its components.",
            )
            return
        label = parts[0].name if len(parts) == 1 else f"{len(parts)} selected items"
        values = SaleDialog.get_sale(label, self)
        if values is None:
            return
        try:
            sell_items([part.id for part in parts], *values)
        except DATA_OPERATION_ERRORS as error:
            show_error(self, "Unable to sell items", error)
            return
        self.data_changed.emit()

    def rename_selected_parts(self):
        parts = self._selected_parts()
        if not parts:
            QMessageBox.information(
                self, "Nothing selected", "Select one or more items to rename."
            )
            return
        initial = parts[0].name if len({part.name for part in parts}) == 1 else ""
        name, accepted = QInputDialog.getText(
            self, "Rename items", "New name", text=initial
        )
        if not accepted:
            return
        try:
            rename_expenses([part.id for part in parts], name)
        except DATA_OPERATION_ERRORS as error:
            show_error(self, "Unable to rename items", error)
            return
        self.data_changed.emit()

    def delete_selected_parts(self):
        parts = self._selected_parts()
        if not parts:
            QMessageBox.information(
                self, "Nothing selected", "Select one or more items to delete."
            )
            return
        if not ask_confirmation(
            self,
            "Delete expenses",
            f"Permanently delete {len(parts)} selected expense record(s)?",
        ):
            return
        try:
            delete_expenses([part.id for part in parts])
        except DATA_OPERATION_ERRORS as error:
            show_error(self, "Unable to delete items", error)
            return
        self.data_changed.emit()

    def sell_selected_pc(self):
        pc = self._selected_pc()
        if pc is None:
            return
        values = SaleDialog.get_sale(pc.name, self)
        if values is None:
            return
        try:
            sell_pc(pc.id, *values)
        except DATA_OPERATION_ERRORS as error:
            show_error(self, "Unable to sell PC", error)
            return
        self.data_changed.emit()

    def rename_selected_pc(self):
        pc = self._selected_pc()
        if pc is None:
            return
        name, accepted = QInputDialog.getText(
            self, "Rename PC", "New name", text=pc.name
        )
        if not accepted:
            return
        try:
            rename_pc(pc.id, name)
        except DATA_OPERATION_ERRORS as error:
            show_error(self, "Unable to rename PC", error)
            return
        self.data_changed.emit()

    def disassemble_selected_pc(self):
        pc = self._selected_pc()
        if pc is None or not ask_confirmation(
            self,
            "Disassemble PC",
            f"Disassemble '{pc.name}' and return its components to stock?",
        ):
            return
        try:
            disassemble_pc(pc.id)
        except DATA_OPERATION_ERRORS as error:
            show_error(self, "Unable to disassemble PC", error)
            return
        self.data_changed.emit()
