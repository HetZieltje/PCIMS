from dataclasses import replace

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGroupBox,
    QHBoxLayout,
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
    LaptopEditDialog,
    LaptopExtractionDialog,
    LaptopReplacementDialog,
    ProofEditDialog,
    SaleDialog,
)
from pcims.app.formatting import format_cents
from pcims.app.table_model import (
    Column,
    ContextAction,
    RecordTableModel,
    configure_context_menu,
    configure_table_view,
    selected_ids,
)
from pcims.app.tasks import TaskManager
from pcims.contracts import LaptopOperations, LaptopSnapshot
from pcims.models import Expense, Laptop, LaptopSlot


def _slot_key(slot: LaptopSlot) -> int:
    return slot.extracted.id


class LaptopPage(AsyncCommandPage):
    data_changed = Signal()

    def __init__(
        self,
        services: LaptopOperations,
        *,
        tasks: TaskManager,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(tasks, parent)
        self.services = services
        self._laptops: dict[int, Laptop] = {}
        self._available: tuple[Expense, ...] = ()
        self._slots: dict[int, tuple[Laptop, LaptopSlot]] = {}

        self.laptop_model = RecordTableModel[Laptop](
            (
                Column("ID", lambda laptop: str(laptop.id), lambda laptop: laptop.id),
                Column(
                    "Model number",
                    lambda laptop: laptop.name,
                    lambda laptop: laptop.name.casefold(),
                ),
                Column(
                    "Purchase price",
                    lambda laptop: format_cents(laptop.original_cost_cents),
                    lambda laptop: laptop.original_cost_cents,
                ),
                Column(
                    "Laptop basis",
                    lambda laptop: format_cents(laptop.item.price_cents),
                    lambda laptop: laptop.item.price_cents,
                ),
                Column(
                    "Current total cost",
                    lambda laptop: format_cents(laptop.current_cost_cents),
                    lambda laptop: laptop.current_cost_cents,
                ),
                Column(
                    "Tracked changes",
                    lambda laptop: str(len(laptop.slots)),
                    lambda laptop: len(laptop.slots),
                ),
                Column(
                    "Status",
                    lambda laptop: "Sold" if laptop.is_sold else "In stock",
                    lambda laptop: "sold" if laptop.is_sold else "stock",
                ),
                Column(
                    "Proofs",
                    lambda laptop: str(len(laptop.item.proofs)),
                    lambda laptop: len(laptop.item.proofs),
                ),
            ),
            lambda laptop: laptop.id,
        )
        self.laptop_table = QTableView()
        configure_table_view(self.laptop_table, self.laptop_model)
        self.laptop_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.laptop_table.selectionModel().selectionChanged.connect(self._render_slots)
        configure_context_menu(
            self.laptop_table,
            (
                ContextAction("Add laptop…", self.add_laptop, lambda: True),
                ContextAction("Edit laptop…", self.edit_laptop, self._has_laptop),
                ContextAction(
                    "Proofs of purchase…", self.edit_proofs, self._has_laptop
                ),
                ContextAction(
                    "Remove or replace component…",
                    self.extract_component,
                    self._can_edit_laptop,
                    True,
                ),
                ContextAction("Sell laptop…", self.sell_laptop, self._can_edit_laptop),
                ContextAction(
                    "Delete laptop…", self.delete_laptop, self._can_edit_laptop, True
                ),
            ),
        )
        laptop_buttons = QHBoxLayout()
        for text, callback in (
            ("Add laptop", self.add_laptop),
            ("Edit", self.edit_laptop),
            ("Proofs…", self.edit_proofs),
            ("Remove / replace part…", self.extract_component),
            ("Sell", self.sell_laptop),
            ("Delete", self.delete_laptop),
        ):
            button = QPushButton(text)
            button.clicked.connect(callback)
            laptop_buttons.addWidget(button)
        laptop_buttons.addStretch()
        laptop_layout = QVBoxLayout()
        laptop_layout.addWidget(self.laptop_table)
        laptop_layout.addLayout(laptop_buttons)
        laptop_box = QGroupBox("Laptops")
        laptop_box.setLayout(laptop_layout)

        self.slot_model = RecordTableModel[tuple[int, LaptopSlot]](
            (
                Column(
                    "Type",
                    lambda row: row[1].component_type,
                    lambda row: row[1].component_type,
                ),
                Column(
                    "Slot",
                    lambda row: str(row[1].slot_number),
                    lambda row: row[1].slot_number,
                ),
                Column(
                    "Removed factory part",
                    lambda row: row[1].extracted.name,
                    lambda row: row[1].extracted.name.casefold(),
                ),
                Column(
                    "Transferred value",
                    lambda row: format_cents(row[1].extracted.price_cents),
                    lambda row: row[1].extracted.price_cents,
                ),
                Column(
                    "Installed replacement",
                    lambda row: row[1].installed.name if row[1].installed else "Empty",
                    lambda row: (
                        row[1].installed.name if row[1].installed else ""
                    ).casefold(),
                ),
                Column(
                    "Replacement cost",
                    lambda row: (
                        format_cents(row[1].installed.price_cents)
                        if row[1].installed
                        else "—"
                    ),
                    lambda row: (
                        row[1].installed.price_cents if row[1].installed else -1
                    ),
                ),
            ),
            lambda row: row[0],
        )
        self.slot_table = QTableView()
        configure_table_view(self.slot_table, self.slot_model, stretch_column=2)
        self.slot_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        configure_context_menu(
            self.slot_table,
            (
                ContextAction(
                    "Change replacement…",
                    self.change_replacement,
                    self._has_editable_slot,
                ),
                ContextAction(
                    "Restore factory component…",
                    self.restore_component,
                    self._has_editable_slot,
                    True,
                ),
            ),
        )
        slot_buttons = QHBoxLayout()
        self.change_replacement_button = QPushButton("Change replacement…")
        self.change_replacement_button.clicked.connect(self.change_replacement)
        self.restore_component_button = QPushButton("Restore factory component…")
        self.restore_component_button.clicked.connect(self.restore_component)
        slot_buttons.addWidget(self.change_replacement_button)
        slot_buttons.addWidget(self.restore_component_button)
        slot_buttons.addStretch()
        self.slot_table.selectionModel().selectionChanged.connect(
            self._update_slot_actions
        )
        self._update_slot_actions()
        slot_layout = QVBoxLayout()
        slot_layout.addWidget(self.slot_table)
        slot_layout.addLayout(slot_buttons)
        slot_box = QGroupBox("Explicit RAM and storage changes")
        slot_box.setLayout(slot_layout)

        self.splitter = QSplitter()
        self.splitter.addWidget(laptop_box)
        self.splitter.addWidget(slot_box)
        self.splitter.setSizes((520, 360))
        layout = QVBoxLayout(self)
        layout.addWidget(self.splitter)

    def load_snapshot(self) -> LaptopSnapshot:
        return self.services.laptop_snapshot()

    def apply_snapshot(self, snapshot: LaptopSnapshot) -> None:
        selected = selected_ids(self.laptop_table)
        self._laptops = {laptop.id: laptop for laptop in snapshot.laptops}
        self._available = snapshot.available_components
        self.laptop_model.set_records(snapshot.laptops)
        target = selected[0] if selected else next(iter(self._laptops), None)
        if target in self._laptops:
            for row, laptop in enumerate(self.laptop_model.records):
                if laptop.id == target:
                    self.laptop_table.selectRow(row)
                    break
        self._render_slots()

    def _selected_laptop(self, *, notify: bool = True) -> Laptop | None:
        identifiers = selected_ids(self.laptop_table)
        laptop = self._laptops.get(identifiers[0]) if len(identifiers) == 1 else None
        if laptop is None and notify:
            QMessageBox.information(
                self, "Select a laptop", "Select exactly one laptop."
            )
        return laptop

    def _has_laptop(self) -> bool:
        return self._selected_laptop(notify=False) is not None

    def _can_edit_laptop(self) -> bool:
        laptop = self._selected_laptop(notify=False)
        return laptop is not None and not laptop.is_sold

    def _render_slots(self, *_: object) -> None:
        laptop = self._selected_laptop(notify=False)
        rows = (
            ()
            if laptop is None
            else tuple((_slot_key(slot), slot) for slot in laptop.slots)
        )
        self._slots = (
            {key: (laptop, slot) for key, slot in rows} if laptop is not None else {}
        )
        self.slot_model.set_records(rows)
        self._update_slot_actions()

    def _selected_slot(self) -> tuple[Laptop, LaptopSlot] | None:
        identifiers = selected_ids(self.slot_table)
        result = self._slots.get(identifiers[0]) if len(identifiers) == 1 else None
        if result is None:
            QMessageBox.information(
                self, "Select a change", "Select one tracked component change."
            )
        return result

    def _has_slot(self) -> bool:
        identifiers = selected_ids(self.slot_table)
        return len(identifiers) == 1 and identifiers[0] in self._slots

    def _has_editable_slot(self) -> bool:
        if not self._has_slot():
            return False
        identifiers = selected_ids(self.slot_table)
        laptop, _slot = self._slots[identifiers[0]]
        return not laptop.is_sold

    def _update_slot_actions(self, *_: object) -> None:
        enabled = self._has_editable_slot()
        self.change_replacement_button.setEnabled(enabled)
        self.restore_component_button.setEnabled(enabled)

    def add_laptop(self) -> None:
        laptop = LaptopEditDialog.get_laptop(parent=self)
        if laptop is None:
            return
        proofs = ProofEditDialog.get_new_proofs(parent=self)
        if proofs is None:
            return
        self.run_command(
            lambda: self.services.add_laptop(laptop, proofs),
            self.data_changed.emit,
            "Unable to add laptop",
        )

    def edit_laptop(self) -> None:
        laptop = self._selected_laptop()
        if laptop is None:
            return
        editable = replace(laptop.item, price_cents=laptop.original_cost_cents)
        replacement = LaptopEditDialog.get_laptop(editable, self)
        if replacement is None:
            return
        self.run_command(
            lambda: self.services.update_laptop(laptop.id, replacement),
            self.data_changed.emit,
            "Unable to edit laptop",
        )

    def edit_proofs(self) -> None:
        laptop = self._selected_laptop()
        if laptop is None:
            return
        update = ProofEditDialog.get_update(
            laptop.item.proofs,
            lambda proof_id: self.services.proof_file(laptop.id, proof_id),
            self,
        )
        if update is None:
            return
        retained, additions = update
        self.run_command(
            lambda: self.services.replace_expense_proofs(
                laptop.id, retained, additions
            ),
            self.data_changed.emit,
            "Unable to update laptop proofs",
        )

    def extract_component(self) -> None:
        laptop = self._selected_laptop()
        if laptop is None or laptop.is_sold:
            return
        values = LaptopExtractionDialog.get_extraction(self._available, self)
        if values is None:
            return
        kind, slot, extracted, installed_id = values
        self.run_command(
            lambda: self.services.extract_laptop_component(
                laptop.id, kind, slot, extracted, installed_id
            ),
            self.data_changed.emit,
            "Unable to change laptop component",
        )

    def change_replacement(self) -> None:
        selected = self._selected_slot()
        if selected is None:
            return
        laptop, slot = selected
        if laptop.is_sold:
            return
        accepted, replacement_id = LaptopReplacementDialog.get_replacement(
            slot.component_type,
            slot.installed.id if slot.installed else None,
            tuple(item for item in self._available if item.id != slot.extracted.id)
            + ((slot.installed,) if slot.installed else ()),
            self,
        )
        if not accepted:
            return
        self.run_command(
            lambda: self.services.set_laptop_replacement(
                laptop.id, slot.component_type, slot.slot_number, replacement_id
            ),
            self.data_changed.emit,
            "Unable to change replacement",
        )

    def restore_component(self) -> None:
        selected = self._selected_slot()
        if selected is None:
            return
        laptop, slot = selected
        if laptop.is_sold:
            return
        if not ask_confirmation(
            self,
            "Restore factory component",
            f"Return '{slot.extracted.name}' to {laptop.name}? The extracted inventory "
            "record will be removed and any replacement will return to stock.",
        ):
            return
        self.run_command(
            lambda: self.services.restore_laptop_component(
                laptop.id, slot.component_type, slot.slot_number
            ),
            self.data_changed.emit,
            "Unable to restore factory component",
        )

    def sell_laptop(self) -> None:
        laptop = self._selected_laptop()
        if laptop is None or laptop.is_sold:
            return
        terms = SaleDialog.get_sale(laptop.name, self)
        if terms is None:
            return
        self.run_command(
            lambda: self.services.sell_laptop(laptop.id, terms),
            self.data_changed.emit,
            "Unable to sell laptop",
        )

    def delete_laptop(self) -> None:
        laptop = self._selected_laptop()
        if (
            laptop is None
            or laptop.is_sold
            or not ask_confirmation(
                self,
                "Delete laptop",
                f"Permanently delete '{laptop.name}'? Tracked factory components must be restored first.",
            )
        ):
            return
        self.run_command(
            lambda: self.services.delete_laptop(laptop.id),
            self.data_changed.emit,
            "Unable to delete laptop",
        )
