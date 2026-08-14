"""Small Qt-native adapter for running blocking application services safely."""

from collections.abc import Callable
from typing import Generic, TypeVar

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal, Slot
from shiboken6 import isValid

from pcims.app.errors import log_exception
from pcims.db.errors import NotFoundError, ValidationError

ResultT = TypeVar("ResultT")


def _log_unexpected(error: Exception) -> None:
    if not isinstance(error, (ValidationError, NotFoundError)):
        log_exception(type(error), error, error.__traceback__)


class TaskSignals(QObject):
    succeeded = Signal(object)
    failed = Signal(object)


class BackgroundTask(QRunnable, Generic[ResultT]):
    """Execute one blocking callable and marshal its outcome to Qt's GUI thread."""

    def __init__(self, operation: Callable[[], ResultT]):
        super().__init__()
        self.operation = operation
        self.signals = TaskSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = self.operation()
        except Exception as error:  # noqa: BLE001 - task boundary reports failures
            _log_unexpected(error)
            self.signals.failed.emit(error)
        else:
            self.signals.succeeded.emit(result)


class TaskManager(QObject):
    """Own every background task for one UI lifetime."""

    became_idle = Signal()

    def __init__(
        self,
        parent: QObject | None = None,
        pool: QThreadPool | None = None,
    ) -> None:
        super().__init__(parent)
        self._pool = pool or QThreadPool.globalInstance()
        self._active: dict[int, object] = {}

    @property
    def active(self) -> bool:
        return bool(self._active)

    @property
    def active_count(self) -> int:
        return len(self._active)

    def run(
        self,
        operation: Callable[[], ResultT],
        on_success: Callable[[ResultT], None],
        on_failure: Callable[[Exception], None],
        *,
        owner: QObject,
    ) -> BackgroundTask[ResultT]:
        task = BackgroundTask(operation)
        task_id = id(task)

        def succeeded(result: ResultT) -> None:
            self._active.pop(task_id, None)
            try:
                if isValid(owner):
                    on_success(result)
            finally:
                if not self._active:
                    self.became_idle.emit()

        def failed(error: Exception) -> None:
            self._active.pop(task_id, None)
            try:
                if isValid(owner):
                    on_failure(error)
            finally:
                if not self._active:
                    self.became_idle.emit()

        task.signals.succeeded.connect(succeeded)
        task.signals.failed.connect(failed)
        self._active[task_id] = task
        try:
            self._pool.start(task)
        except Exception as error:  # noqa: BLE001 - Qt submission boundary
            _log_unexpected(error)
            QTimer.singleShot(0, lambda caught=error: task.signals.failed.emit(caught))
        return task
