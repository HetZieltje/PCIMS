from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from pcims.app.assembly_model import AssemblyTreeModel
from pcims.app.async_page import AsyncCommandPage
from pcims.app.tasks import TaskManager
from pcims.contracts import AssembleSnapshot, AssemblyOperations


class AssemblePage(AsyncCommandPage):
    data_changed = Signal()

    def __init__(
        self,
        services: AssemblyOperations,
        *,
        tasks: TaskManager,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(tasks, parent)
        self.services = services
        self.name = QLineEdit()
        self.name.setMaximumWidth(360)
        self.tree_model = AssemblyTreeModel()
        self.tree = QTreeView()
        self.tree.setModel(self.tree_model)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self.tree.header().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
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

    def refresh(self) -> None:
        self.apply_snapshot(self.load_snapshot())

    def load_snapshot(self) -> AssembleSnapshot:
        return self.services.assemble_snapshot()

    def apply_snapshot(self, snapshot: AssembleSnapshot) -> None:
        self.tree_model.set_records(snapshot.available_inventory)
        self.tree.expandAll()
        if not self.name.text().strip():
            self.name.setText(self._next_name(snapshot.pc_names))

    @staticmethod
    def _next_name(pc_names: tuple[str, ...]) -> str:
        names = set(pc_names)
        index = 1
        while f"PC {index}" in names:
            index += 1
        return f"PC {index}"

    def assemble(self) -> None:
        ids = self.tree_model.checked_ids
        if not ids:
            QMessageBox.warning(self, "No components", "Select at least one component.")
            return
        name = self.name.text()
        self.run_command(
            lambda: self.services.assemble_pc(name, ids),
            self._assembly_recorded,
            "Unable to assemble PC",
        )

    def _show_context_menu(self, position: QPoint) -> None:
        index = self.tree.indexAt(position)
        expense_ids = self.tree_model.expense_ids_at(index)
        if not expense_ids:
            return
        menu = self._build_context_menu(expense_ids)
        try:
            menu.exec(self.tree.viewport().mapToGlobal(position))
        finally:
            menu.deleteLater()

    def _build_context_menu(self, expense_ids: tuple[int, ...]) -> QMenu:
        menu = QMenu(self.tree)
        select = menu.addAction(
            "Select component" if len(expense_ids) == 1 else "Select category"
        )
        select.setEnabled(
            any(item_id not in self.tree_model.checked_ids for item_id in expense_ids)
        )
        select.triggered.connect(
            lambda: self.tree_model.set_expenses_checked(expense_ids, checked=True)
        )
        deselect = menu.addAction(
            "Deselect component" if len(expense_ids) == 1 else "Deselect category"
        )
        deselect.setEnabled(
            any(item_id in self.tree_model.checked_ids for item_id in expense_ids)
        )
        deselect.triggered.connect(
            lambda: self.tree_model.set_expenses_checked(expense_ids, checked=False)
        )
        menu.addSeparator()
        clear = menu.addAction("Clear all selections")
        clear.setEnabled(bool(self.tree_model.checked_ids))
        clear.triggered.connect(
            lambda: self.tree_model.set_expenses_checked(
                self.tree_model.checked_ids, checked=False
            )
        )
        return menu

    def _assembly_recorded(self) -> None:
        self.name.clear()
        self.data_changed.emit()
