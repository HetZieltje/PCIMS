"""Crash-safe SQLite backup support."""

import os
import shutil
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime
from pathlib import Path

from db.connection import (configure_database, connection, get_database_path)


REQUIRED_TABLES = {"expenses", "assembled_pcs", "income", "sold_pcs"}


def _validate_database(path):
    with closing(sqlite3.connect(f"file:{Path(path).resolve().as_posix()}?mode=ro", uri=True)) as database:
        result = database.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise sqlite3.DatabaseError(f"Database integrity check failed: {result}")
        tables = {row[0] for row in database.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    missing = REQUIRED_TABLES - tables
    if missing:
        raise sqlite3.DatabaseError(
            f"Backup is missing required tables: {', '.join(sorted(missing))}"
        )


def create_backup(destination_directory=None, keep=14):
    """Create and verify a backup, then retain only the newest *keep* files."""
    if keep < 1:
        raise ValueError("At least one backup must be retained.")
    destination = Path(destination_directory or get_database_path().parent / "backups").resolve()
    destination.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
    final_path = destination / f"pcims_db_{stamp}.db"
    temporary_path = final_path.with_suffix(".tmp")

    try:
        with connection() as source:
            with closing(sqlite3.connect(temporary_path)) as target:
                with target:
                    source.backup(target)
        _validate_database(temporary_path)
        os.replace(temporary_path, final_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()

    backups = sorted(destination.glob("pcims_db_*.db"), key=lambda path: path.stat().st_mtime, reverse=True)
    for old_backup in backups[keep:]:
        old_backup.unlink()
    return final_path


def restore_backup(backup_path, pre_restore_directory=None):
    """Validate and atomically restore a backup, preserving the current DB first."""
    backup_path = Path(backup_path).expanduser().resolve()
    live_path = get_database_path().resolve()
    if not backup_path.is_file():
        raise FileNotFoundError(f"Backup does not exist: {backup_path}")
    if backup_path == live_path:
        raise ValueError("The active database cannot be restored over itself.")
    _validate_database(backup_path)

    temporary_path = live_path.with_name(f".{live_path.name}.{uuid.uuid4().hex}.restore.tmp")
    try:
        # Stage first: creating the safety snapshot applies retention and may
        # legitimately prune the selected source when it lives in that folder.
        shutil.copy2(backup_path, temporary_path)
        _validate_database(temporary_path)

        # Upgrade the staged database before it becomes active.
        from db.queries import initialize_database

        configure_database(temporary_path)
        initialize_database()
        _validate_database(temporary_path)
        configure_database(live_path)
        safety_backup = create_backup(pre_restore_directory)
        os.replace(temporary_path, live_path)
    except Exception:
        configure_database(live_path)
        raise
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
    return safety_backup
