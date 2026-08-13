"""Last-resort logging and reporting for unexpected GUI exceptions."""

import sys
import traceback
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox

from db.connection import get_data_dir


def install_exception_hook(log_path=None):
    """Install a GUI-safe exception hook and return the previous hook."""
    previous_hook = sys.excepthook
    destination = Path(log_path or get_data_dir() / "pcims-errors.log").resolve()

    def report_exception(exception_type, exception, traceback_object):
        if issubclass(exception_type, KeyboardInterrupt):
            previous_hook(exception_type, exception, traceback_object)
            return
        logged = False
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("a", encoding="utf-8") as log_file:
                log_file.write(f"\n[{datetime.now().astimezone().isoformat()}]\n")
                traceback.print_exception(
                    exception_type, exception, traceback_object, file=log_file
                )
            logged = True
        except OSError:
            traceback.print_exception(exception_type, exception, traceback_object)

        application = QApplication.instance()
        if application is not None:
            detail = f"\n\nDetails were written to:\n{destination}" if logged else ""
            try:
                QMessageBox.critical(
                    application.activeWindow(),
                    "Unexpected error",
                    "PCIMS encountered an unexpected error. The current operation was stopped."
                    f"{detail}",
                )
            except RuntimeError:
                traceback.print_exception(exception_type, exception, traceback_object)

    sys.excepthook = report_exception
    return previous_hook
