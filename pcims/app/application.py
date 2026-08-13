"""PCIMS Qt application bootstrap."""

import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from PySide6.QtCore import QLockFile
from PySide6.QtWidgets import QApplication, QMessageBox, QStyleFactory

from pcims.app.errors import install_exception_hook
from pcims.app.main_window import MainWindow
from pcims.db.connection import get_database_path
from pcims.db.errors import DatabaseIntegrityError, SchemaVersionError
from pcims.services import default_services


def create_application(argv: Sequence[str] | None = None) -> QApplication:
    existing = QApplication.instance()
    application = (
        cast(QApplication, existing)
        if existing is not None
        else QApplication(list(argv) if argv is not None else sys.argv)
    )
    application.setApplicationName("PCIMS")
    application.setApplicationDisplayName("PC Inventory Management")
    application.setOrganizationName("PCIMS")
    fusion = QStyleFactory.create("Fusion")
    if fusion is not None:
        application.setStyle(fusion)
    return application


def acquire_instance_lock(database_path: Path | None = None) -> QLockFile | None:
    """Lock one configured database so two stale GUI sessions cannot race."""
    database_path = (database_path or get_database_path()).resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(database_path.with_suffix(database_path.suffix + ".lock")))
    lock.setStaleLockTime(30_000)
    return lock if lock.tryLock(0) else None


def main(argv: Sequence[str] | None = None) -> int:
    application = create_application(argv)
    services = default_services()
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
            services.initialize()
        except (
            OSError,
            DatabaseIntegrityError,
            SchemaVersionError,
            sqlite3.DatabaseError,
        ) as error:
            QMessageBox.critical(None, "Database unavailable", str(error))
            return 2

        try:
            window = MainWindow(services)
        except (OSError, sqlite3.DatabaseError) as error:
            QMessageBox.critical(
                None,
                "Database unavailable",
                f"PCIMS could not load the database:\n\n{error}",
            )
            return 2
        window.show()
        window.create_startup_backup()
        return application.exec()
    finally:
        instance_lock.unlock()


if __name__ == "__main__":
    raise SystemExit(main())
