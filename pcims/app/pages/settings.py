from collections.abc import Callable

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from pcims.app.common import ask_confirmation, show_error
from pcims.app.tasks import TaskManager
from pcims.contracts import (
    BackupResult,
    MaintenanceOperations,
    RestoreResult,
    StorageSummary,
)


def _format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("bytes", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:,.0f} {unit}" if unit == "bytes" else f"{value:,.1f} {unit}"
        value /= 1024
    raise AssertionError("unreachable")


class SettingsPage(QWidget):
    database_restored = Signal()
    theme_changed = Signal(str)
    backup_retention_changed = Signal(int)
    storage_changed = Signal()
    laptops_enabled_changed = Signal(bool)

    def __init__(
        self,
        services: MaintenanceOperations,
        *,
        tasks: TaskManager,
        theme: str = "system",
        backup_retention: int = 14,
        laptops_enabled: bool = False,
        has_pending_changes: Callable[[], bool] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.tasks = tasks
        self.services = services
        self._has_pending_changes = has_pending_changes or (lambda: False)
        database_path = self.services.database_path
        path_label = QLabel(str(database_path))
        path_label.setTextInteractionFlags(
            path_label.textInteractionFlags()
            | Qt.TextInteractionFlag.TextSelectableByMouse
        )
        open_folder = QPushButton("Open data folder")
        open_folder.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(database_path.parent))
            )
        )
        location_row = QHBoxLayout()
        location_row.addWidget(path_label, 1)
        location_row.addWidget(open_folder)
        location_widget = QWidget()
        location_widget.setLayout(location_row)

        self.theme = QComboBox()
        self.theme.addItem("Follow system", "system")
        self.theme.addItem("Light", "light")
        self.theme.addItem("Dark", "dark")
        index = self.theme.findData(theme)
        self.theme.setCurrentIndex(max(0, index))
        self.theme.currentIndexChanged.connect(
            lambda: self.theme_changed.emit(str(self.theme.currentData()))
        )
        appearance_form = QFormLayout()
        appearance_form.addRow("Theme", self.theme)
        appearance_box = QGroupBox("Appearance")
        appearance_box.setLayout(appearance_form)

        self.laptops_enabled = QCheckBox("Enable laptop inventory")
        self.laptops_enabled.setChecked(laptops_enabled)
        self.laptops_enabled.setToolTip(
            "Adds a dedicated Laptops tab. Factory RAM and storage remain unindexed "
            "until you explicitly remove or replace them."
        )
        self.laptops_enabled.toggled.connect(self.laptops_enabled_changed.emit)
        features_layout = QVBoxLayout()
        features_layout.addWidget(self.laptops_enabled)
        features_note = QLabel(
            "When enabled, laptops are kept separate from component and assembled-PC "
            "workflows. You can turn the tab off without deleting its data."
        )
        features_note.setWordWrap(True)
        features_layout.addWidget(features_note)
        features_box = QGroupBox("Optional features")
        features_box.setLayout(features_layout)

        self.backup_button = QPushButton("Create backup now")
        self.backup_button.clicked.connect(self.create_backup)
        self.restore_button = QPushButton("Restore backup…")
        self.restore_button.clicked.connect(self.restore_backup)
        self.export_button = QPushButton("Export purchases and sales…")
        self.export_button.clicked.connect(self.export_csv)
        self.backup_retention = QSpinBox()
        self.backup_retention.setRange(1, 30)
        self.backup_retention.setValue(backup_retention)
        self.backup_retention.setSuffix(" backups")
        self.backup_retention.valueChanged.connect(self.backup_retention_changed.emit)
        self.database_usage = QLabel("Loading…")
        self.proof_usage = QLabel("Loading…")
        self.backup_usage = QLabel("Loading…")
        maintenance_form = QFormLayout()
        maintenance_form.addRow("Database", location_widget)
        maintenance_form.addRow("Backup", self.backup_button)
        maintenance_form.addRow("Keep automatic backups", self.backup_retention)
        maintenance_form.addRow("Database storage", self.database_usage)
        maintenance_form.addRow("Proof storage", self.proof_usage)
        maintenance_form.addRow("Automatic backup storage", self.backup_usage)
        maintenance_form.addRow("Restore", self.restore_button)
        maintenance_form.addRow("CSV export", self.export_button)
        maintenance_box = QGroupBox("Data and backups")
        maintenance_box.setLayout(maintenance_form)

        note = QLabel(
            "PCIMS automatically migrates databases created from the clean Qt baseline. "
            "Unrelated and legacy Tkinter database formats remain unsupported. Stored proofs "
            "are limited to 512 MiB in total."
        )
        note.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(appearance_box)
        layout.addWidget(features_box)
        layout.addWidget(maintenance_box)
        layout.addWidget(note)
        layout.addStretch()

    def load_snapshot(self) -> StorageSummary:
        return self.services.storage_summary()

    def apply_snapshot(self, summary: StorageSummary) -> None:
        self.database_usage.setText(_format_bytes(summary.database_bytes))
        self.proof_usage.setText(
            f"{_format_bytes(summary.proof_bytes)} in {summary.proof_count} file(s)"
        )
        self.backup_usage.setText(
            f"{_format_bytes(summary.backup_bytes)} in {summary.backup_count} backup(s)"
        )

    def create_backup(self) -> None:
        keep = self.backup_retention.value()
        self.backup_button.setEnabled(False)
        self.backup_button.setText("Creating backup…")
        self._backup_task = self.tasks.run(
            lambda: self.services.create_backup(keep=keep),
            self._backup_finished,
            self._backup_failed,
            owner=self,
        )

    def export_csv(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self, "Choose export folder", str(self.services.database_path.parent)
        )
        if not directory:
            return
        self.export_button.setEnabled(False)
        self._export_task = self.tasks.run(
            lambda: self.services.export_csv(directory),
            self._export_finished,
            self._export_failed,
            owner=self,
        )

    def _export_finished(self, paths: tuple[object, object]) -> None:
        self.export_button.setEnabled(True)
        QMessageBox.information(
            self,
            "Export complete",
            f"Created:\n{paths[0]}\n{paths[1]}",
        )

    def _export_failed(self, error: Exception) -> None:
        self.export_button.setEnabled(True)
        show_error(self, "Export failed", error)

    def _backup_finished(self, backup: BackupResult) -> None:
        self.backup_button.setEnabled(True)
        self.backup_button.setText("Create backup now")
        self.storage_changed.emit()
        if backup.has_warnings:
            QMessageBox.warning(
                self,
                "Backup complete with warning",
                f"Backup saved to:\n{backup.path}\n\n"
                f"Backup warnings:\n{backup.warning_text}",
            )
            return
        message = (
            f"No data changed; the existing verified backup is current:\n{backup.path}"
            if backup.reused
            else f"Backup saved to:\n{backup.path}"
        )
        QMessageBox.information(self, "Backup complete", message)

    def _backup_failed(self, error: Exception) -> None:
        self.backup_button.setEnabled(True)
        self.backup_button.setText("Create backup now")
        show_error(self, "Backup failed", error)

    def restore_backup(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select PCIMS backup",
            str(self.services.database_path.parent / "backups"),
            "SQLite databases (*.db);;All files (*)",
        )
        if not path:
            return
        message = "Replace the active database? A safety backup will be created first."
        if self._has_pending_changes():
            message += "\n\nUnrecorded purchase lines will be discarded after a successful restore."
        if not ask_confirmation(self, "Restore backup", message):
            return
        self.window().setEnabled(False)
        self.restore_button.setText("Restoring backup…")
        keep = self.backup_retention.value()
        self._restore_task = self.tasks.run(
            lambda: self.services.restore_backup(path, keep=keep),
            self._restore_finished,
            self._restore_failed,
            owner=self,
        )

    def _restore_failed(self, error: Exception) -> None:
        self.window().setEnabled(True)
        self.restore_button.setText("Restore backup…")
        show_error(self, "Restore failed", error)

    def _restore_finished(self, result: RestoreResult) -> None:
        self.window().setEnabled(True)
        self.restore_button.setText("Restore backup…")
        self.database_restored.emit()
        self.storage_changed.emit()
        warning_note = (
            f"\n\nRecovery warnings:\n{result.warning_text}"
            if result.has_warnings
            else ""
        )
        message_box = (
            QMessageBox.warning if result.has_warnings else QMessageBox.information
        )
        message_box(
            self,
            "Restore complete with warning"
            if result.has_warnings
            else "Restore complete",
            f"The database was restored from:\n{result.source_path}\n\n"
            "Previous data was saved to:\n"
            f"{result.safety_backup.path}"
            f"{warning_note}",
        )
