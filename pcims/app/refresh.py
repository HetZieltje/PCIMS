"""Coalesced asynchronous loading for independently refreshable pages."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TypeVar, cast

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget

from pcims.app.tasks import BackgroundTask, TaskManager

SnapshotT = TypeVar("SnapshotT")


@dataclass(frozen=True, slots=True)
class RefreshBinding:
    page: QWidget
    load: Callable[[], object]
    apply: Callable[[object], None]
    fail: Callable[[Exception], None] | None = None


def bind_refresh(
    page: QWidget,
    load: Callable[[], SnapshotT],
    apply: Callable[[SnapshotT], None],
    fail: Callable[[Exception], None] | None = None,
) -> RefreshBinding:
    """Erase one snapshot type only at Qt's dynamic callback boundary."""

    def apply_typed(snapshot: object) -> None:
        apply(cast(SnapshotT, snapshot))

    return RefreshBinding(page, load, apply_typed, fail)


@dataclass(slots=True)
class _RefreshState:
    requested_generation: int = 0
    running_generation: int | None = None
    task: BackgroundTask[object] | None = None
    pending: bool = False


class RefreshCoordinator(QObject):
    """Own refresh invalidation, coalescing, and stale-result suppression."""

    refreshed = Signal()
    failed = Signal(object, object)

    def __init__(
        self,
        tasks: TaskManager,
        bindings: Iterable[RefreshBinding],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._tasks = tasks
        self._bindings = {binding.page: binding for binding in bindings}
        self._states = {page: _RefreshState() for page in self._bindings}
        self._dirty_pages = set(self._bindings)
        self._accepting = True

    @property
    def active(self) -> bool:
        return any(state.task is not None for state in self._states.values())

    @property
    def accepting(self) -> bool:
        return self._accepting

    def is_dirty(self, page: QWidget) -> bool:
        return page in self._dirty_pages

    def mark_dirty(self, page: QWidget) -> None:
        if page in self._states:
            self._dirty_pages.add(page)

    def start_if_dirty(self, page: QWidget) -> None:
        if page in self._dirty_pages:
            self.start(page)

    def start(self, page: QWidget) -> None:
        if not self._accepting:
            return
        state = self._states.get(page)
        if state is None:
            return
        state.requested_generation += 1
        if state.task is not None:
            state.pending = True
            return
        self._launch(page, state)

    def invalidate_all(self, visible_page: QWidget | None = None) -> None:
        for state in self._states.values():
            state.requested_generation += 1
        self._dirty_pages.update(self._states)
        if self._accepting and visible_page is not None:
            self.start_if_dirty(visible_page)

    def refresh_all(self) -> None:
        if not self._accepting:
            return
        self._dirty_pages.update(self._states)
        for page in self._states:
            self.start(page)

    def pause(self) -> None:
        self._accepting = False
        for page, state in self._states.items():
            if state.task is not None:
                self._dirty_pages.add(page)
            state.requested_generation += 1
            state.pending = False

    def resume(self) -> None:
        self._accepting = True

    def _launch(self, page: QWidget, state: _RefreshState) -> None:
        generation = state.requested_generation
        state.running_generation = generation
        state.pending = False
        state.task = self._tasks.run(
            self._bindings[page].load,
            lambda snapshot: self._succeeded(page, generation, snapshot),
            lambda error: self._failed(page, generation, error),
            owner=self,
        )

    def _succeeded(self, page: QWidget, generation: int, snapshot: object) -> None:
        state = self._finish(page, generation)
        if state is None:
            return
        if state.requested_generation == generation:
            self._bindings[page].apply(snapshot)
            self._dirty_pages.discard(page)
            self.refreshed.emit()
        self._launch_pending(page, state)

    def _failed(self, page: QWidget, generation: int, error: Exception) -> None:
        state = self._finish(page, generation)
        if state is None:
            return
        if state.requested_generation == generation:
            self._dirty_pages.add(page)
            failure_handler = self._bindings[page].fail
            if failure_handler is not None:
                failure_handler(error)
            self.failed.emit(page, error)
        self._launch_pending(page, state)

    def _finish(self, page: QWidget, generation: int) -> _RefreshState | None:
        state = self._states.get(page)
        if state is None or state.running_generation != generation:
            return None
        state.task = None
        state.running_generation = None
        return state

    def _launch_pending(self, page: QWidget, state: _RefreshState) -> None:
        if state.pending:
            self._launch(page, state)
