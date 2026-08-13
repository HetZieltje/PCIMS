from collections import defaultdict

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.common import ID_ROLE, show_error
from app.formatting import format_cents
from db.queries import ITEM_TYPES, assemble_pc, list_inventory, list_pcs


class AssemblePage(QWidget):
    data_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.name = QLineEdit()
        self.name.setMaximumWidth(360)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(("Component", "Cost", "Purchased", "ID"))
        self.tree.setAlternatingRowColors(True)
        self.tree.setColumnHidden(3, True)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        assemble_button = QPushButton("Assemble selected components")
        assemble_button.clicked.connect(self.assemble)

        header = QHBoxLayout()
        header.addWidget(QLabel("PC name"))
        header.addWidget(self.name)
        header.addStretch()
        header.addWidget(assemble_button)
        layout = QVBoxLayout(self)
        layout.addLayout(header)
        layout.addWidget(self.tree)
        self.refresh()

    def refresh(self):
        selected_ids = {
            item.data(0, ID_ROLE)
            for index in range(self.tree.topLevelItemCount())
            for item in self._children(self.tree.topLevelItem(index))
            if item.checkState(0) == Qt.CheckState.Checked
        }
        grouped = defaultdict(list)
        for expense in list_inventory(available_only=True):
            grouped[expense.item_type].append(expense)
        self.tree.clear()
        for item_type in ITEM_TYPES:
            if not grouped[item_type]:
                continue
            group = QTreeWidgetItem(
                (f"{item_type} ({len(grouped[item_type])})", "", "", "")
            )
            group.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.tree.addTopLevelItem(group)
            for expense in grouped[item_type]:
                child = QTreeWidgetItem(
                    (
                        expense.name,
                        format_cents(expense.price_cents),
                        expense.purchase_date.isoformat(),
                        str(expense.id),
                    )
                )
                child.setData(0, ID_ROLE, expense.id)
                child.setFlags(
                    child.flags()
                    | Qt.ItemFlag.ItemIsUserCheckable
                    | Qt.ItemFlag.ItemIsSelectable
                )
                child.setCheckState(
                    0,
                    Qt.CheckState.Checked
                    if expense.id in selected_ids
                    else Qt.CheckState.Unchecked,
                )
                group.addChild(child)
            group.setExpanded(True)
        if not self.name.text().strip():
            self.name.setText(self._next_name())

    @staticmethod
    def _children(parent):
        return [parent.child(index) for index in range(parent.childCount())]

    def _next_name(self):
        names = {pc.name for pc in list_pcs()}
        index = 1
        while f"PC {index}" in names:
            index += 1
        return f"PC {index}"

    def assemble(self):
        ids = [
            child.data(0, ID_ROLE)
            for group_index in range(self.tree.topLevelItemCount())
            for child in self._children(self.tree.topLevelItem(group_index))
            if child.checkState(0) == Qt.CheckState.Checked
        ]
        if not ids:
            QMessageBox.warning(self, "No components", "Select at least one component.")
            return
        try:
            assemble_pc(self.name.text(), ids)
        except (ValueError, LookupError) as error:
            show_error(self, "Unable to assemble PC", error)
            return
        self.name.clear()
        self.data_changed.emit()
