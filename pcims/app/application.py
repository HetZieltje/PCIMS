"""PCIMS Qt application bootstrap."""

import os
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from PySide6.QtCore import QLockFile, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox, QStyleFactory

from pcims.app.errors import install_exception_hook
from pcims.app.main_window import MainWindow
from pcims.db.errors import DatabaseIntegrityError, SchemaVersionError
from pcims.services import ApplicationServices, default_services
from pcims.version import application_version


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
    application.setApplicationVersion(application_version())
    fusion = QStyleFactory.create("Fusion")
    if fusion is not None:
        application.setStyle(fusion)
    return application


def acquire_instance_lock(database_path: Path) -> QLockFile | None:
    """Lock one configured database so two stale GUI sessions cannot race."""
    database_path = database_path.resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(database_path.with_suffix(database_path.suffix + ".lock")))
    lock.setStaleLockTime(30_000)
    if lock.tryLock(0):
        return lock
    error = lock.error()
    if error == QLockFile.LockError.LockFailedError:
        return None
    message = f"PCIMS could not create its instance lock beside {database_path}."
    if error == QLockFile.LockError.PermissionError:
        raise PermissionError(message)
    raise OSError(message)


def _run_application(
    application: QApplication,
    services: ApplicationServices,
    *,
    packaged_smoke_test: bool = False,
) -> int:
    try:
        instance_lock = acquire_instance_lock(services.database_path)
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
        if packaged_smoke_test:
            QTimer.singleShot(30_000, lambda: application.exit(4))
            if window.tasks.active:
                window.tasks.became_idle.connect(application.quit)
            else:
                QTimer.singleShot(0, application.quit)
        else:
            window.create_startup_backup()
        return application.exec()
    finally:
        instance_lock.unlock()


def main(
    argv: Sequence[str] | None = None,
    services: ApplicationServices | None = None,
) -> int:
    application = create_application(argv)
    previous_hook = install_exception_hook()
    try:
        try:
            active_services = services or default_services()
            return _run_application(
                application,
                active_services,
                packaged_smoke_test=os.environ.get("PCIMS_PACKAGED_SMOKE_TEST") == "1",
            )
        except Exception as error:  # noqa: BLE001 - application bootstrap boundary
            sys.excepthook(type(error), error, error.__traceback__)
            return 1
    finally:
        sys.excepthook = previous_hook


if __name__ == "__main__":
    raise SystemExit(main())
