"""In-process coordination for live database operations and replacement."""

import threading
import weakref
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_registry_lock = threading.Lock()
_registry: weakref.WeakValueDictionary[Path, "DatabaseGate"] = (
    weakref.WeakValueDictionary()
)


def gate_for(path: Path) -> "DatabaseGate":
    """Return one live coordination gate for each resolved database identity."""
    resolved = path.resolve()
    with _registry_lock:
        return _registry.setdefault(resolved, DatabaseGate())


class DatabaseGate:
    """Writer-preferring gate with shared operations and exclusive replacement."""

    def __init__(self) -> None:
        self._condition = threading.Condition(threading.Lock())
        self._maintenance_lock = threading.RLock()
        self._readers = 0
        self._reader_depth: dict[int, int] = {}
        self._writer_active = False
        self._writer_thread_id: int | None = None
        self._writer_depth = 0
        self._writers_waiting = 0

    @contextmanager
    def maintenance(self) -> Iterator[None]:
        """Serialize backup-directory work while permitting ordinary concurrency."""
        with self._maintenance_lock:
            yield

    @contextmanager
    def shared(self) -> Iterator[None]:
        thread_id = threading.get_ident()
        with self._condition:
            while (self._writer_active and self._writer_thread_id != thread_id) or (
                self._writers_waiting
                and self._reader_depth.get(thread_id, 0) == 0
                and self._writer_thread_id != thread_id
            ):
                self._condition.wait()
            self._readers += 1
            self._reader_depth[thread_id] = self._reader_depth.get(thread_id, 0) + 1
        try:
            yield
        finally:
            with self._condition:
                self._readers -= 1
                depth = self._reader_depth[thread_id] - 1
                if depth:
                    self._reader_depth[thread_id] = depth
                else:
                    del self._reader_depth[thread_id]
                if self._readers == 0:
                    self._condition.notify_all()

    @contextmanager
    def exclusive(self) -> Iterator[None]:
        thread_id = threading.get_ident()
        with self._condition:
            if self._writer_thread_id == thread_id:
                self._writer_depth += 1
            else:
                if self._reader_depth.get(thread_id, 0):
                    raise RuntimeError(
                        "A shared database operation cannot be upgraded."
                    )
                self._writers_waiting += 1
                try:
                    while self._writer_active or self._readers:
                        self._condition.wait()
                    self._writer_active = True
                    self._writer_thread_id = thread_id
                    self._writer_depth = 1
                finally:
                    self._writers_waiting -= 1
        try:
            yield
        finally:
            with self._condition:
                self._writer_depth -= 1
                if self._writer_depth == 0:
                    self._writer_active = False
                    self._writer_thread_id = None
                    self._condition.notify_all()
