from collections.abc import Callable

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pcims.app.common import ask_confirmation, show_error
from pcims.app.tasks import run_in_background
from pcims.db.backup import BackupResult
from pcims.services import ApplicationServices, default_services


class SettingsPage(QWidget):
    database_restored = Signal()
    theme_changed = Signal(str)

    def __init__(
        self,
        services: ApplicationServices | None = None,
        theme: str = "system",
        has_pending_changes: Callable[[], bool] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.services = services or default_services()
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

        self.backup_button = QPushButton("Create backup now")
        self.backup_button.clicked.connect(self.create_backup)
        self.restore_button = QPushButton("Restore backup…")
        self.restore_button.clicked.connect(self.restore_backup)
        maintenance_form = QFormLayout()
        maintenance_form.addRow("Database", location_widget)
        maintenance_form.addRow("Backup", self.backup_button)
        maintenance_form.addRow("Restore", self.restore_button)
        maintenance_box = QGroupBox("Data and backups")
        maintenance_box.setLayout(maintenance_form)

        note = QLabel(
            "PCIMS uses one current database format. Backups from older application schemas "
            "are intentionally rejected rather than converted at runtime."
        )
        note.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(appearance_box)
        layout.addWidget(maintenance_box)
        layout.addWidget(note)
        layout.addStretch()

    def create_backup(self) -> None:
        self.backup_button.setEnabled(False)
        self.backup_button.setText("Creating backup…")
        self._backup_task = run_in_background(
            self.services.create_backup,
            self._backup_finished,
            self._backup_failed,
        )

    def _backup_finished(self, backup: BackupResult) -> None:
        self.backup_button.setEnabled(True)
        self.backup_button.setText("Create backup now")
        if backup.has_cleanup_warnings:
            QMessageBox.warning(
                self,
                "Backup complete with warning",
                f"Backup saved to:\n{backup.path}\n\n"
                f"Some old backups could not be removed:\n{backup.cleanup_warning}",
            )
            return
        QMessageBox.information(
            self, "Backup complete", f"Backup saved to:\n{backup}"
        )

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
        self._restore_task = run_in_background(
            lambda: self.services.restore_backup(path),
            self._restore_finished,
            self._restore_failed,
        )

    def _restore_failed(self, error: Exception) -> None:
        self.window().setEnabled(True)
        self.restore_button.setText("Restore backup…")
        show_error(self, "Restore failed", error)

    def _restore_finished(self, safety: BackupResult) -> None:
        self.window().setEnabled(True)
        self.restore_button.setText("Restore backup…")
        self.database_restored.emit()
        cleanup_note = (
            f"\n\nSome old backups could not be removed:\n{safety.cleanup_warning}"
            if safety.has_cleanup_warnings
            else ""
        )
        message_box = (
            QMessageBox.warning
            if safety.has_cleanup_warnings
            else QMessageBox.information
        )
        message_box(
            self,
            "Restore complete with warning"
            if safety.has_cleanup_warnings
            else "Restore complete",
            f"The database was restored. Previous data was saved to:\n{safety}"
            f"{cleanup_note}",
        )
