"""SQLite connection configuration for PCIMS."""

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from pcims.db.gate import DatabaseGate, gate_for


def _unicode_nocase(left: str, right: str) -> int:
    left_folded = left.casefold()
    right_folded = right.casefold()
    return (left_folded > right_folded) - (left_folded < right_folded)


def register_database_collations(connection: sqlite3.Connection) -> None:
    """Install the deterministic Unicode comparison used by schema constraints."""
    connection.create_collation("PCIMS_NOCASE", _unicode_nocase)


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


def ensure_private_directory(path: str | os.PathLike[str]) -> Path:
    """Create one application-owned directory and restrict POSIX access."""
    resolved = Path(path).expanduser().resolve()
    resolved.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        resolved.chmod(0o700)
    return resolved


@dataclass(frozen=True, slots=True)
class Database:
    """One explicit SQLite database location and its connection policy."""

    path: Path

    @property
    def gate(self) -> DatabaseGate:
        """Return the process-wide coordination gate for this database path."""
        return gate_for(self.path)

    @classmethod
    def at(cls, path: str | os.PathLike[str]) -> "Database":
        return cls(Path(path).expanduser().resolve())

    def connect(self, *, create: bool = False) -> sqlite3.Connection:
        """Open an existing database, unless creation is explicitly requested."""
        if create:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            database = sqlite3.connect(self.path, timeout=10)
        else:
            database = sqlite3.connect(
                f"{self.path.as_uri()}?mode=rw", uri=True, timeout=10
            )
        try:
            if os.name != "nt":
                self.path.chmod(0o600)
            database.row_factory = sqlite3.Row
            register_database_collations(database)
            database.execute("PRAGMA foreign_keys = ON")
            database.execute("PRAGMA busy_timeout = 10000")
            database.execute("PRAGMA synchronous = FULL")
            database.execute("PRAGMA trusted_schema = OFF")
        except BaseException:
            database.close()
            raise
        return database

    @contextmanager
    def transaction(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        """Yield a snapshot read or immediately locked write transaction."""
        with self.gate.shared():
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


def default_database() -> Database:
    """Build an explicit override or protect the platform-default data location."""
    configured_path = os.environ.get("PCIMS_DB_PATH")
    if configured_path:
        return Database.at(configured_path)
    return Database.at(ensure_private_directory(get_data_dir()) / "pcims.db")
