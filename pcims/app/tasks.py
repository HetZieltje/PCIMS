"""Small Qt-native adapter for running blocking application services safely."""

from collections.abc import Callable
from typing import Generic, TypeVar

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from pcims.app.errors import log_exception

ResultT = TypeVar("ResultT")


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
            log_exception(type(error), error, error.__traceback__)
            self.signals.failed.emit(error)
        else:
            self.signals.succeeded.emit(result)


def run_in_background(
    operation: Callable[[], ResultT],
    on_success: Callable[[ResultT], None],
    on_failure: Callable[[Exception], None],
    *,
    pool: QThreadPool | None = None,
) -> BackgroundTask[ResultT]:
    task = BackgroundTask(operation)
    task.signals.succeeded.connect(on_success)
    task.signals.failed.connect(on_failure)
    (pool or QThreadPool.globalInstance()).start(task)
    return task
