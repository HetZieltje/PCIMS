"""Optional, clearable activity history page."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QTableView, QVBoxLayout, QWidget

from pcims.app.async_page import AsyncCommandPage
from pcims.app.common import ask_confirmation
from pcims.app.table_model import Column, RecordTableModel, configure_table_view
from pcims.app.tasks import TaskManager
from pcims.contracts import ActivityOperations
from pcims.models import AuditEvent


class ActivityPage(AsyncCommandPage):
    data_changed = Signal()

    def __init__(
        self,
        services: ActivityOperations,
        *,
        tasks: TaskManager,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(tasks, parent)
        self.services = services
        self.model = RecordTableModel[AuditEvent](
            (
                Column("ID", lambda event: str(event.id), lambda event: event.id),
                Column(
                    "Time",
                    lambda event: event.occurred_at.replace("T", " "),
                    lambda event: event.occurred_at,
                ),
                Column(
                    "Action", lambda event: event.action, lambda event: event.action
                ),
                Column(
                    "Record",
                    lambda event: (
                        event.entity_type
                        if event.entity_id is None
                        else f"{event.entity_type} #{event.entity_id}"
                    ),
                    lambda event: f"{event.entity_type}:{event.entity_id or 0}",
                ),
                Column(
                    "Details", lambda event: event.summary, lambda event: event.summary
                ),
            ),
            lambda event: event.id,
        )
        self.table = QTableView()
        configure_table_view(self.table, self.model)
        self.table.setColumnHidden(0, True)
        layout = QVBoxLayout(self)
        actions = QHBoxLayout()
        actions.addStretch()
        clear_button = QPushButton("Clear activity")
        clear_button.clicked.connect(self.clear_activity)
        actions.addWidget(clear_button)
        layout.addLayout(actions)
        layout.addWidget(self.table)

    def load_snapshot(self) -> tuple[AuditEvent, ...]:
        return self.services.list_activity(500)

    def apply_snapshot(self, events: tuple[AuditEvent, ...]) -> None:
        self.model.set_records(events)

    def clear_activity(self) -> None:
        if not ask_confirmation(
            self,
            "Clear activity",
            "Clear the activity list? Inventory, PCs, purchases, and sales are unaffected.",
        ):
            return
        self.run_command(
            self.services.clear_activity,
            self.data_changed.emit,
            "Unable to clear activity",
        )
