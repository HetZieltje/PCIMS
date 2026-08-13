"""PCIMS Qt application bootstrap."""

import sqlite3
import sys

from PySide6.QtWidgets import QApplication, QMessageBox, QStyleFactory

from app.main_window import MainWindow
from db.backup import create_backup
from db.queries import SchemaVersionError, initialize_database


def create_application(argv=None):
    application = QApplication.instance() or QApplication(argv or sys.argv)
    application.setApplicationName("PCIMS")
    application.setApplicationDisplayName("PC Inventory Management")
    application.setOrganizationName("PCIMS")
    fusion = QStyleFactory.create("Fusion")
    if fusion is not None:
        application.setStyle(fusion)
    return application


def main(argv=None):
    application = create_application(argv)
    try:
        initialize_database()
    except SchemaVersionError as error:
        QMessageBox.critical(None, "Incompatible database", str(error))
        return 2

    backup_warning = None
    try:
        create_backup()
    except (OSError, ValueError, sqlite3.DatabaseError) as error:
        backup_warning = str(error)

    window = MainWindow()
    window.show()
    if backup_warning:
        QMessageBox.warning(
            window,
            "Backup warning",
            f"PCIMS started, but the startup backup failed:\n\n{backup_warning}",
        )
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
