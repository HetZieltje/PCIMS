"""SQLite connection configuration for PCIMS."""

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path


def get_data_dir():
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


_database_path = Path(
    os.environ.get("PCIMS_DB_PATH", get_data_dir() / "pcims.db")
).expanduser().resolve()


def get_database_path():
    return _database_path


def configure_database(path):
    """Point future connections at *path* (primarily for isolated tests)."""
    global _database_path
    _database_path = Path(path).expanduser().resolve()


def get_connection():
    _database_path.parent.mkdir(parents=True, exist_ok=True)
    database = sqlite3.connect(_database_path, timeout=10)
    database.row_factory = sqlite3.Row
    database.execute("PRAGMA foreign_keys = ON")
    database.execute("PRAGMA busy_timeout = 10000")
    return database


@contextmanager
def connection():
    """Yield one transactional connection and always close it."""
    database = get_connection()
    try:
        with database:
            yield database
    finally:
        database.close()
