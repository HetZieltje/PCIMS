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

from pcims.app.common import DATA_OPERATION_ERRORS, show_error
from pcims.app.formatting import format_cents
from pcims.app.table_model import ID_ROLE
from pcims.db.models import Expense
from pcims.domain import ITEM_TYPES, ItemType
from pcims.services import ApplicationServices, default_services


class AssemblePage(QWidget):
    data_changed = Signal()

    def __init__(
        self,
        services: ApplicationServices | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.services = services or default_services()
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

    def refresh(self) -> None:
        selected_ids = {
            int(item.data(0, ID_ROLE))
            for index in range(self.tree.topLevelItemCount())
            for item in self._children(self.tree.topLevelItem(index))
            if item.checkState(0) == Qt.CheckState.Checked
        }
        grouped: defaultdict[ItemType, list[Expense]] = defaultdict(list)
        for expense in self.services.list_inventory(available_only=True):
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
    def _children(parent: QTreeWidgetItem | None) -> list[QTreeWidgetItem]:
        if parent is None:
            return []
        return [parent.child(index) for index in range(parent.childCount())]

    def _next_name(self) -> str:
        names = {pc.name for pc in self.services.list_pcs()}
        index = 1
        while f"PC {index}" in names:
            index += 1
        return f"PC {index}"

    def assemble(self) -> None:
        ids = [
            int(child.data(0, ID_ROLE))
            for group_index in range(self.tree.topLevelItemCount())
            for child in self._children(self.tree.topLevelItem(group_index))
            if child.checkState(0) == Qt.CheckState.Checked
        ]
        if not ids:
            QMessageBox.warning(self, "No components", "Select at least one component.")
            return
        try:
            self.services.assemble_pc(self.name.text(), ids)
        except DATA_OPERATION_ERRORS as error:
            show_error(self, "Unable to assemble PC", error)
            return
        self.name.clear()
        self.data_changed.emit()
