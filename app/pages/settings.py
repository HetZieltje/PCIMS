import sqlite3

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

from app.common import ask_confirmation, show_error
from db.backup import create_backup, restore_backup
from db.connection import get_database_path


class SettingsPage(QWidget):
    data_changed = Signal()
    theme_changed = Signal(str)

    def __init__(self, theme="system", parent=None):
        super().__init__(parent)
        database_path = get_database_path()
        path_label = QLabel(str(database_path))
        path_label.setTextInteractionFlags(
            path_label.textInteractionFlags() | Qt.TextInteractionFlag.TextSelectableByMouse
        )
        open_folder = QPushButton("Open data folder")
        open_folder.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(database_path.parent)))
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
            lambda: self.theme_changed.emit(self.theme.currentData())
        )
        appearance_form = QFormLayout()
        appearance_form.addRow("Theme", self.theme)
        appearance_box = QGroupBox("Appearance")
        appearance_box.setLayout(appearance_form)

        backup_button = QPushButton("Create backup now")
        backup_button.clicked.connect(self.create_backup)
        restore_button = QPushButton("Restore backup…")
        restore_button.clicked.connect(self.restore_backup)
        maintenance_form = QFormLayout()
        maintenance_form.addRow("Database", location_widget)
        maintenance_form.addRow("Backup", backup_button)
        maintenance_form.addRow("Restore", restore_button)
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

    def create_backup(self):
        try:
            path = create_backup()
        except (OSError, ValueError, sqlite3.DatabaseError) as error:
            show_error(self, "Backup failed", error)
            return
        QMessageBox.information(self, "Backup complete", f"Backup saved to:\n{path}")

    def restore_backup(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select PCIMS backup",
            str(get_database_path().parent / "backups"),
            "SQLite databases (*.db);;All files (*)",
        )
        if not path or not ask_confirmation(
            self,
            "Restore backup",
            "Replace the active database? A safety backup will be created first.",
        ):
            return
        try:
            safety = restore_backup(path)
        except (OSError, ValueError, sqlite3.DatabaseError) as error:
            show_error(self, "Restore failed", error)
            return
        self.data_changed.emit()
        QMessageBox.information(
            self,
            "Restore complete",
            f"The database was restored. Previous data was saved to:\n{safety}",
        )
