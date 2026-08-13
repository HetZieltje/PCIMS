"""SQLite connection configuration for PCIMS."""

import os
import sqlite3
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path


def get_data_dir() -> Path:
    """Return the per-user writable PCIMS data directory."""
    configured = os.environ.get("PCIMS_DATA_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return (Path(local_app_data) / "PCIMS").resolve()
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    if xdg_data_home:
        return (Path(xdg_data_home) / "pcims").expanduser().resolve()
    return (Path.home() / ".local" / "share" / "pcims").resolve()


@dataclass(frozen=True, slots=True)
class Database:
    """One explicit SQLite database location and its connection policy."""

    path: Path

    @classmethod
    def at(cls, path: str | os.PathLike[str]) -> "Database":
        return cls(Path(path).expanduser().resolve())

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        database = sqlite3.connect(self.path, timeout=10)
        database.row_factory = sqlite3.Row
        database.execute("PRAGMA foreign_keys = ON")
        database.execute("PRAGMA busy_timeout = 10000")
        database.execute("PRAGMA synchronous = FULL")
        database.execute("PRAGMA trusted_schema = OFF")
        return database

    @contextmanager
    def transaction(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        """Yield a snapshot read or immediately locked write transaction."""
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()


_configured_path = os.environ.get("PCIMS_DB_PATH")
_database = Database.at(_configured_path or get_data_dir() / "pcims.db")


def get_database() -> Database:
    return _database


def get_database_path() -> Path:
    return get_database().path


def configure_database(path: str | os.PathLike[str]) -> Database:
    """Select the process database at the composition boundary and return it."""
    global _database
    _database = Database.at(path)
    return _database


def get_connection() -> sqlite3.Connection:
    return get_database().connect()


def connection() -> AbstractContextManager[sqlite3.Connection]:
    return get_database().transaction()
