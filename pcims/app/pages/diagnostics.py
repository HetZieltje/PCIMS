"""On-demand application health and runtime diagnostics."""

from collections.abc import Callable

from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from pcims.app.common import show_error
from pcims.app.table_model import Column, RecordTableModel, configure_table_view
from pcims.app.tasks import TaskManager
from pcims.contracts import DiagnosticCheck, DiagnosticsSnapshot, MaintenanceOperations


class DiagnosticsPage(QWidget):
    def __init__(
        self,
        services: MaintenanceOperations,
        *,
        tasks: TaskManager,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.services = services
        self.tasks = tasks
        self.summary = QLabel("Open this tab to run the lightweight health check.")
        self.summary.setWordWrap(True)
        self.full_check = QPushButton("Run full integrity check")
        self.full_check.setToolTip(
            "Also reads and hashes every stored proof of purchase."
        )
        self.full_check.clicked.connect(self.run_full_check)
        self.copy_report = QPushButton("Copy report")
        self.copy_report.setEnabled(False)
        self.copy_report.clicked.connect(self._copy_report)
        actions = QHBoxLayout()
        actions.addWidget(self.full_check)
        actions.addWidget(self.copy_report)
        actions.addStretch()

        self.model: RecordTableModel[DiagnosticCheck] = RecordTableModel(
            (
                Column("Check", lambda row: row.name, lambda row: row.name.casefold()),
                Column(
                    "Status", lambda row: row.status, lambda row: row.status.casefold()
                ),
                Column(
                    "Details", lambda row: row.detail, lambda row: row.detail.casefold()
                ),
                Column(
                    "Time",
                    lambda row: f"{row.duration_ms} ms",
                    lambda row: row.duration_ms,
                ),
            ),
            lambda row: row.id,
        )
        self.table = QTableView()
        configure_table_view(self.table, self.model, stretch_column=2)
        self.startup = QLabel("Startup measurements are not available yet.")
        self.startup.setWordWrap(True)
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setPlaceholderText("No application log entries.")
        self.logs.setMaximumBlockCount(500)

        layout = QVBoxLayout(self)
        layout.addWidget(self.summary)
        layout.addLayout(actions)
        layout.addWidget(self.table, 2)
        layout.addWidget(QLabel("Startup timeline"))
        layout.addWidget(self.startup)
        layout.addWidget(QLabel("Recent application log"))
        layout.addWidget(self.logs, 1)
        self._snapshot: DiagnosticsSnapshot | None = None
        self._requested_thorough = False
        self._request_coordinated_refresh: Callable[[], None] | None = None

    def set_refresh_request(self, request: Callable[[], None]) -> None:
        self._request_coordinated_refresh = request

    def load_snapshot(self) -> DiagnosticsSnapshot:
        return self.services.diagnostics_snapshot(thorough=self._requested_thorough)

    def apply_snapshot(self, snapshot: DiagnosticsSnapshot) -> None:
        self._snapshot = snapshot
        self.model.set_records(snapshot.checks)
        failed = sum(check.status == "Failed" for check in snapshot.checks)
        warnings = sum(
            check.status in {"Warning", "Not fully checked"}
            for check in snapshot.checks
        )
        self.summary.setText(
            f"Checked {snapshot.generated_at:%Y-%m-%d %H:%M:%S}: "
            f"{failed} failure(s), {warnings} warning(s)."
        )
        self.startup.setText(
            "  •  ".join(
                f"{stage.name}: {stage.elapsed_ms} ms" for stage in snapshot.startup
            )
            or "Startup measurements are not available yet."
        )
        self.logs.setPlainText(snapshot.log_tail)
        self.copy_report.setEnabled(True)
        self._requested_thorough = False
        self.full_check.setEnabled(True)
        self.full_check.setText("Run full integrity check")

    def run_full_check(self) -> None:
        self.full_check.setEnabled(False)
        self.full_check.setText("Checking all proofs…")
        self._requested_thorough = True
        if self._request_coordinated_refresh is not None:
            self._request_coordinated_refresh()
            return
        self._diagnostic_task = self.tasks.run(
            lambda: self.services.diagnostics_snapshot(thorough=True),
            self._full_check_finished,
            self._full_check_failed,
            owner=self,
        )

    def _full_check_finished(self, snapshot: DiagnosticsSnapshot) -> None:
        self.full_check.setEnabled(True)
        self.full_check.setText("Run full integrity check")
        self.apply_snapshot(snapshot)

    def _full_check_failed(self, error: Exception) -> None:
        self.coordinated_refresh_failed(error)
        show_error(self, "Diagnostics failed", error)

    def coordinated_refresh_failed(self, _error: Exception) -> None:
        self._requested_thorough = False
        self.full_check.setEnabled(True)
        self.full_check.setText("Run full integrity check")

    def _copy_report(self) -> None:
        if self._snapshot is None:
            return
        snapshot = self._snapshot
        lines = [
            f"PCIMS diagnostics — {snapshot.generated_at:%Y-%m-%d %H:%M:%S}",
            *(
                f"{check.name}: {check.status} — {check.detail}"
                for check in snapshot.checks
            ),
            "Startup: "
            + ", ".join(
                f"{stage.name} {stage.elapsed_ms} ms" for stage in snapshot.startup
            ),
        ]
        QApplication.clipboard().setText("\n".join(lines))
        self.copy_report.setText("Copied")
