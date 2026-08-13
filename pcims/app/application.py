"""PCIMS Qt application bootstrap."""

import sqlite3
import sys

from PySide6.QtCore import QLockFile
from PySide6.QtWidgets import QApplication, QMessageBox, QStyleFactory

from pcims.app.errors import install_exception_hook
from pcims.app.main_window import MainWindow
from pcims.db.backup import create_backup
from pcims.db.connection import get_database_path
from pcims.db.queries import (
    DatabaseIntegrityError,
    SchemaVersionError,
    initialize_database,
)


def create_application(argv=None):
    application = QApplication.instance() or QApplication(argv or sys.argv)
    application.setApplicationName("PCIMS")
    application.setApplicationDisplayName("PC Inventory Management")
    application.setOrganizationName("PCIMS")
    fusion = QStyleFactory.create("Fusion")
    if fusion is not None:
        application.setStyle(fusion)
    return application


def acquire_instance_lock(database_path=None):
    """Lock one configured database so two stale GUI sessions cannot race."""
    database_path = (database_path or get_database_path()).resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(database_path.with_suffix(database_path.suffix + ".lock")))
    lock.setStaleLockTime(30_000)
    return lock if lock.tryLock(0) else None


def main(argv=None):
    application = create_application(argv)
    install_exception_hook()
    try:
        instance_lock = acquire_instance_lock()
    except OSError as error:
        QMessageBox.critical(
            None,
            "Data directory unavailable",
            f"PCIMS could not access its data directory:\n\n{error}",
        )
        return 2
    if instance_lock is None:
        QMessageBox.critical(
            None,
            "PCIMS is already running",
            "Another PCIMS window is already using this database.",
        )
        return 3
    try:
        try:
            initialize_database()
        except (
            OSError,
            DatabaseIntegrityError,
            SchemaVersionError,
            sqlite3.DatabaseError,
        ) as error:
            QMessageBox.critical(None, "Database unavailable", str(error))
            return 2

        backup_warning = None
        try:
            backup = create_backup()
            if backup.has_cleanup_warnings:
                backup_warning = (
                    f"The backup was created at {backup.path}, but old backup cleanup failed:\n\n"
                    f"{backup.cleanup_warning}"
                )
        except (OSError, ValueError, sqlite3.DatabaseError) as error:
            backup_warning = f"The startup backup could not be created:\n\n{error}"

        try:
            window = MainWindow()
        except (OSError, sqlite3.DatabaseError) as error:
            QMessageBox.critical(
                None,
                "Database unavailable",
                f"PCIMS could not load the database:\n\n{error}",
            )
            return 2
        window.show()
        if backup_warning:
            QMessageBox.warning(
                window,
                "Backup warning",
                f"PCIMS started with a backup warning:\n\n{backup_warning}",
            )
        return application.exec()
    finally:
        instance_lock.unlock()


if __name__ == "__main__":
    raise SystemExit(main())
