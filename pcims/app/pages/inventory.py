from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from pcims.app.async_page import AsyncCommandPage
from pcims.app.common import ask_confirmation
from pcims.app.dialogs import (
    ExpenseEditDialog,
    PCEditDialog,
    ProofEditDialog,
    SaleDialog,
)
from pcims.app.formatting import format_cents
from pcims.app.table_model import (
    Column,
    RecordTableModel,
    configure_table_view,
    selected_ids,
)
from pcims.app.tasks import TaskManager
from pcims.contracts import InventoryOperations, InventorySnapshot
from pcims.domain import ITEM_TYPES
from pcims.models import AssembledPC, Expense


def _component_summary(pc: AssembledPC) -> str:
    counts: dict[str, int] = {}
    for part in pc.parts:
        counts[part.item_type] = counts.get(part.item_type, 0) + 1
    return ", ".join(
        f"{count}× {item_type}" if count > 1 else item_type
        for item_type, count in counts.items()
    )


class InventoryPage(AsyncCommandPage):
    data_changed = Signal()

    def __init__(
        self,
        services: InventoryOperations,
        *,
        tasks: TaskManager,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(tasks, parent)
        self.services = services
        self._all_parts: tuple[Expense, ...] = ()
        self._parts: dict[int, Expense] = {}
        self._pcs: dict[int, AssembledPC] = {}
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

        self.parts_model = RecordTableModel[Expense](
            (
                Column("ID", lambda item: str(item.id), lambda item: item.id),
                Column(
                    "Name", lambda item: item.name, lambda item: item.name.casefold()
                ),
                Column(
                    "Type", lambda item: item.item_type, lambda item: item.item_type
                ),
                Column(
                    "Cost",
                    lambda item: format_cents(item.price_cents),
                    lambda item: item.price_cents,
                ),
                Column(
                    "Purchased",
                    lambda item: item.purchase_date.isoformat(),
                    lambda item: item.purchase_date.toordinal(),
                ),
                Column(
                    "Serial",
                    lambda item: item.details.serial_number,
                    lambda item: item.details.serial_number.casefold(),
                ),
                Column(
                    "Location",
                    lambda item: item.details.storage_location,
                    lambda item: item.details.storage_location.casefold(),
                ),
                Column(
                    "Status",
                    lambda item: item.pc_name or "Available",
                    lambda item: (item.pc_name or "Available").casefold(),
                ),
                Column(
                    "Proofs",
                    lambda item: str(len(item.proofs)),
                    lambda item: len(item.proofs),
                ),
            ),
            lambda item: item.id,
        )
        self.parts_table = QTableView()
        configure_table_view(self.parts_table, self.parts_model)
        part_buttons = QHBoxLayout()
        for text, callback in (
            ("Sell selected", self.sell_selected_parts),
            ("Edit component", self.edit_selected_part),
            ("Proofs…", self.edit_selected_proofs),
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

        self.pc_model = RecordTableModel[AssembledPC](
            (
                Column("ID", lambda pc: str(pc.id), lambda pc: pc.id),
                Column("Name", lambda pc: pc.name, lambda pc: pc.name.casefold()),
                Column(
                    "Cost",
                    lambda pc: format_cents(pc.cost_cents),
                    lambda pc: pc.cost_cents,
                ),
                Column(
                    "Components",
                    _component_summary,
                    lambda pc: _component_summary(pc).casefold(),
                ),
            ),
            lambda pc: pc.id,
        )
        self.pc_table = QTableView()
        configure_table_view(self.pc_table, self.pc_model)
        pc_buttons = QHBoxLayout()
        for text, callback in (
            ("Sell PC", self.sell_selected_pc),
            ("Edit PC", self.edit_selected_pc),
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

    def refresh(self) -> None:
        self.apply_snapshot(self.load_snapshot())

    def load_snapshot(self) -> InventorySnapshot:
        return self.services.inventory_snapshot()

    def apply_snapshot(self, snapshot: InventorySnapshot) -> None:
        self._all_parts = snapshot.inventory
        self._render_parts()
        self._render_pcs(snapshot.pcs)

    def _apply_filters(self, *_: object) -> None:
        self._render_parts()

    def _render_parts(self) -> None:
        search = self.search.text().strip().casefold()
        item_type = self.type_filter.currentData()
        status = self.status_filter.currentData()
        parts = self._all_parts
        if item_type is not None:
            parts = tuple(item for item in parts if item.item_type == item_type)
        if search:
            parts = tuple(
                item
                for item in parts
                if any(
                    search in value.casefold()
                    for value in (
                        item.name,
                        item.details.vendor,
                        item.details.serial_number,
                        item.details.storage_location,
                        item.details.notes,
                    )
                )
            )
        if status == "available":
            parts = tuple(item for item in parts if item.is_available)
        elif status == "assigned":
            parts = tuple(item for item in parts if item.pc_id is not None)
        self._parts = {item.id: item for item in parts}
        self.parts_model.set_records(parts)

    def _render_pcs(self, pcs: tuple[AssembledPC, ...]) -> None:
        self._pcs = {pc.id: pc for pc in pcs}
        self.pc_model.set_records(pcs)

    def _selected_parts(self) -> list[Expense]:
        ids = selected_ids(self.parts_table)
        parts = [self._parts[item_id] for item_id in ids if item_id in self._parts]
        return parts if len(parts) == len(ids) else []

    def _selected_part(self) -> Expense | None:
        parts = self._selected_parts()
        if len(parts) != 1:
            QMessageBox.information(
                self, "Select one component", "Select exactly one component to edit."
            )
            return None
        return parts[0]

    def _selected_pc(self) -> AssembledPC | None:
        ids = selected_ids(self.pc_table)
        if len(ids) != 1:
            QMessageBox.information(
                self, "Select one PC", "Select exactly one assembled PC."
            )
            return None
        pc = self._pcs.get(ids[0])
        if pc is None:
            QMessageBox.information(
                self, "Selection changed", "That PC is no longer in the current view."
            )
        return pc

    def sell_selected_parts(self) -> None:
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
        self.run_command(
            lambda: self.services.sell_items([part.id for part in parts], values),
            self.data_changed.emit,
            "Unable to sell items",
        )

    def edit_selected_part(self) -> None:
        part = self._selected_part()
        if part is None:
            return
        replacement = ExpenseEditDialog.get_expense(part, self)
        if replacement is None:
            return
        self.run_command(
            lambda: self.services.update_expense(part.id, replacement),
            self.data_changed.emit,
            "Unable to edit component",
        )

    def edit_selected_proofs(self) -> None:
        part = self._selected_part()
        if part is None:
            return
        update = ProofEditDialog.get_update(
            part.proofs,
            lambda proof_id: self.services.proof_file(part.id, proof_id),
            self,
        )
        if update is None:
            return
        retained_ids, new_proofs = update
        if retained_ids == tuple(proof.id for proof in part.proofs) and not new_proofs:
            return
        self.run_command(
            lambda: self.services.replace_expense_proofs(
                part.id, retained_ids, new_proofs
            ),
            self.data_changed.emit,
            "Unable to update proofs",
        )

    def delete_selected_parts(self) -> None:
        parts = self._selected_parts()
        if not parts:
            QMessageBox.information(
                self, "Nothing selected", "Select one or more items to delete."
            )
            return
        if not ask_confirmation(
            self,
            "Delete items",
            f"Permanently delete {len(parts)} selected inventory item(s)?",
        ):
            return
        self.run_command(
            lambda: self.services.delete_expenses([part.id for part in parts]),
            self.data_changed.emit,
            "Unable to delete items",
        )

    def sell_selected_pc(self) -> None:
        pc = self._selected_pc()
        if pc is None:
            return
        values = SaleDialog.get_sale(pc.name, self)
        if values is None:
            return
        self.run_command(
            lambda: self.services.sell_pc(pc.id, values),
            self.data_changed.emit,
            "Unable to sell PC",
        )

    def edit_selected_pc(self) -> None:
        pc = self._selected_pc()
        if pc is None:
            return
        candidates = tuple(
            part for part in self._all_parts if part.is_available or part.pc_id == pc.id
        )
        values = PCEditDialog.get_pc(pc, candidates, self)
        if values is None:
            return
        name, expense_ids = values
        self.run_command(
            lambda: self.services.update_pc(pc.id, name, expense_ids),
            self.data_changed.emit,
            "Unable to edit PC",
        )

    def disassemble_selected_pc(self) -> None:
        pc = self._selected_pc()
        if pc is None or not ask_confirmation(
            self,
            "Disassemble PC",
            f"Disassemble '{pc.name}' and return its components to stock?",
        ):
            return
        self.run_command(
            lambda: self.services.disassemble_pc(pc.id),
            self.data_changed.emit,
            "Unable to disassemble PC",
        )
