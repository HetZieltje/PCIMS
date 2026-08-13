"""SQLite connection configuration for PCIMS."""

import os
import shutil
import sqlite3
from contextlib import contextmanager
from pathlib import Path


def get_data_dir():
    """Return PCIMS' per-user writable data directory."""
    configured = os.environ.get("PCIMS_DATA_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return (Path(base) / "PCIMS").resolve()
    return (Path.home() / ".pcims").resolve()


_database_path = Path(os.environ.get("PCIMS_DB_PATH", get_data_dir() / "pcims_db.db")).expanduser().resolve()


def get_database_path():
    """Return the database file used by new connections."""
    return _database_path


def configure_database(path):
    """Configure a database path, allowing tests to use a temporary file."""
    global _database_path
    _database_path = Path(path).expanduser().resolve()


def get_connection():
    """Create a configured SQLite connection."""
    _database_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path = Path(__file__).with_name("pcims_db.db").resolve()
    if not _database_path.exists() and legacy_path.exists() and legacy_path != _database_path:
        shutil.copy2(legacy_path, _database_path)
    connection = sqlite3.connect(_database_path, timeout=10)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    return connection


@contextmanager
def connection():
    """Yield one transactional connection and always close its file handle."""
    database = get_connection()
    try:
        with database:
            yield database
    finally:
        database.close()
