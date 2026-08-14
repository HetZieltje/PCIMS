from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from pcims.app.assembly_model import AssemblyTreeModel
from pcims.app.async_page import AsyncCommandPage
from pcims.app.tasks import TaskManager
from pcims.services import ApplicationServices, AssembleSnapshot, default_services


class AssemblePage(AsyncCommandPage):
    data_changed = Signal()

    def __init__(
        self,
        services: ApplicationServices | None = None,
        parent: QWidget | None = None,
        tasks: TaskManager | None = None,
    ) -> None:
        super().__init__(parent, tasks)
        self.services = services or default_services()
        self.name = QLineEdit()
        self.name.setMaximumWidth(360)
        self.tree_model = AssemblyTreeModel()
        self.tree = QTreeView()
        self.tree.setModel(self.tree_model)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
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

    def _assembly_recorded(self) -> None:
        self.name.clear()
        self.data_changed.emit()
