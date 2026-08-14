"""Reusable non-blocking command boundary for data-changing Qt pages."""

from collections.abc import Callable

from PySide6.QtWidgets import QWidget

from pcims.app.common import show_error
from pcims.app.tasks import BackgroundTask, TaskManager


class AsyncCommandPage(QWidget):
    """A page that runs at most one blocking mutation outside the GUI thread."""

    def __init__(
        self,
        tasks: TaskManager,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.tasks = tasks
        self._command_task: BackgroundTask[object] | None = None

    @property
    def command_running(self) -> bool:
        return self._command_task is not None

    def run_command(
        self,
        operation: Callable[[], object],
        on_success: Callable[[], None],
        error_title: str,
    ) -> bool:
        if self._command_task is not None:
            return False
        self.setEnabled(False)
        self._command_task = self.tasks.run(
            operation,
            lambda _result: self._command_succeeded(on_success),
            lambda error: self._command_failed(error_title, error),
            owner=self,
        )
        return True

    def _command_succeeded(self, on_success: Callable[[], None]) -> None:
        self._command_task = None
        self.setEnabled(True)
        on_success()

    def _command_failed(self, error_title: str, error: Exception) -> None:
        self._command_task = None
        self.setEnabled(True)
        show_error(self, error_title, error)
