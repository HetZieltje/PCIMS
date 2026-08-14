"""In-process coordination for live database operations and replacement."""

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_registry_lock = threading.Lock()
_registry: dict[Path, "DatabaseGate"] = {}


def gate_for(path: Path) -> "DatabaseGate":
    """Return one coordination gate for each resolved database identity."""
    resolved = path.resolve()
    with _registry_lock:
        return _registry.setdefault(resolved, DatabaseGate())


class DatabaseGate:
    """Writer-preferring gate with shared operations and exclusive replacement."""

    def __init__(self) -> None:
        self._condition = threading.Condition(threading.Lock())
        self._readers = 0
        self._writer_active = False
        self._writers_waiting = 0

    @contextmanager
    def shared(self) -> Iterator[None]:
        with self._condition:
            while self._writer_active or self._writers_waiting:
                self._condition.wait()
            self._readers += 1
        try:
            yield
        finally:
            with self._condition:
                self._readers -= 1
                if self._readers == 0:
                    self._condition.notify_all()

    @contextmanager
    def exclusive(self) -> Iterator[None]:
        with self._condition:
            self._writers_waiting += 1
            try:
                while self._writer_active or self._readers:
                    self._condition.wait()
                self._writer_active = True
            finally:
                self._writers_waiting -= 1
        try:
            yield
        finally:
            with self._condition:
                self._writer_active = False
                self._condition.notify_all()
