"""Last-resort logging and reporting for unexpected GUI exceptions."""

import os
import sys
import threading
import traceback
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import cast

from PySide6.QtWidgets import QApplication, QMessageBox

from pcims.db.connection import ensure_private_directory, get_data_dir

ExceptionHook = Callable[
    [type[BaseException], BaseException, TracebackType | None], None
]
MAX_ERROR_LOG_BYTES = 1_000_000
_log_lock = threading.Lock()


def log_exception(
    exception_type: type[BaseException],
    exception: BaseException,
    traceback_object: TracebackType | None,
    log_path: str | Path | None = None,
) -> Path | None:
    """Append one traceback to a bounded, thread-safe diagnostic log."""
    try:
        if log_path is None:
            destination = (
                ensure_private_directory(get_data_dir()) / "pcims-errors.log"
            )
        else:
            destination = Path(log_path).resolve()
        with _log_lock:
            destination.parent.mkdir(parents=True, exist_ok=True)
            rotation_warning: str | None = None
            if (
                destination.is_file()
                and destination.stat().st_size >= MAX_ERROR_LOG_BYTES
            ):
                try:
                    destination.replace(
                        destination.with_name(f"{destination.name}.1")
                    )
                except OSError as rotation_error:
                    rotation_warning = f"Log rotation failed: {rotation_error}"
            with destination.open("a", encoding="utf-8") as log_file:
                if os.name != "nt":
                    destination.chmod(0o600)
                log_file.write(f"\n[{datetime.now().astimezone().isoformat()}]\n")
                if rotation_warning:
                    log_file.write(f"{rotation_warning}\n")
                traceback.print_exception(
                    exception_type, exception, traceback_object, file=log_file
                )
        return destination
    except (OSError, ValueError):
        traceback.print_exception(exception_type, exception, traceback_object)
        return None


def install_exception_hook(
    log_path: str | Path | None = None,
) -> ExceptionHook:
    """Install a GUI-safe exception hook and return the previous hook."""
    previous_hook = sys.excepthook

    def report_exception(
        exception_type: type[BaseException],
        exception: BaseException,
        traceback_object: TracebackType | None,
    ) -> None:
        if issubclass(exception_type, KeyboardInterrupt):
            previous_hook(exception_type, exception, traceback_object)
            return
        destination = log_exception(
            exception_type, exception, traceback_object, log_path
        )

        application = QApplication.instance()
        if isinstance(application, QApplication):
            detail = (
                f"\n\nDetails were written to:\n{destination}" if destination else ""
            )
            try:
                QMessageBox.critical(
                    QApplication.activeWindow(),
                    "Unexpected error",
                    "PCIMS encountered an unexpected error. The current operation was stopped."
                    f"{detail}",
                )
            except RuntimeError:
                traceback.print_exception(exception_type, exception, traceback_object)

    sys.excepthook = report_exception
    return cast(ExceptionHook, previous_hook)
