"""Verified backup and atomic restore for the current PCIMS schema."""

import os
import shutil
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pcims.db.connection import connection, get_database_path
from pcims.db.queries import (
    REQUIRED_TABLES,
    REQUIRED_TRIGGERS,
    SCHEMA_COLUMNS,
    SCHEMA_VERSION,
    DatabaseIntegrityError,
    validate_current_data,
)


@dataclass(frozen=True, slots=True)
class BackupResult(os.PathLike):
    """A verified backup plus any non-fatal retention cleanup failures."""

    path: Path
    cleanup_errors: tuple[str, ...] = ()

    def __fspath__(self):
        return str(self.path)

    def __str__(self):
        return str(self.path)

    @property
    def has_cleanup_warnings(self):
        return bool(self.cleanup_errors)

    @property
    def cleanup_warning(self):
        return "\n".join(self.cleanup_errors)


def validate_database(path):
    resolved = Path(path).expanduser().resolve()
    with closing(
        sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True)
    ) as database:
        integrity = database.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise sqlite3.DatabaseError(f"Database integrity check failed: {integrity}")
        foreign_key_violations = database.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_violations:
            table, row_id, referenced_table, _ = foreign_key_violations[0]
            raise sqlite3.DatabaseError(
                f"Database foreign-key check failed at {table} row {row_id} "
                f"(missing {referenced_table} record)."
            )
        version = database.execute("PRAGMA user_version").fetchone()[0]
        if version != SCHEMA_VERSION:
            raise sqlite3.DatabaseError(
                f"Backup schema {version} is incompatible with required schema {SCHEMA_VERSION}."
            )
        tables = {
            row[0]
            for row in database.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if tables != REQUIRED_TABLES:
            missing = REQUIRED_TABLES - tables
            extra = tables - REQUIRED_TABLES
            differences = []
            if missing:
                differences.append(f"missing {', '.join(sorted(missing))}")
            if extra:
                differences.append(f"unexpected {', '.join(sorted(extra))}")
            raise sqlite3.DatabaseError(
                f"Database has incompatible tables: {'; '.join(differences)}"
            )
        for table, expected in SCHEMA_COLUMNS.items():
            actual = tuple(
                row[1] for row in database.execute(f'PRAGMA table_info("{table}")')
            )
            if actual != expected:
                raise sqlite3.DatabaseError(
                    f"Database table '{table}' has an incompatible layout."
                )
        triggers = {
            row[0]
            for row in database.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            )
        }
        if triggers != REQUIRED_TRIGGERS:
            raise sqlite3.DatabaseError(
                "Database integrity triggers do not match the current schema."
            )
        try:
            validate_current_data(database)
        except DatabaseIntegrityError as error:
            raise sqlite3.DatabaseError(str(error)) from error


def create_backup(destination_directory=None, keep=14):
    if keep < 1:
        raise ValueError("At least one backup must be retained.")
    destination = (
        Path(destination_directory or get_database_path().parent / "backups")
        .expanduser()
        .resolve()
    )
    destination.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
    final_path = destination / f"pcims_{stamp}.db"
    temporary_path = final_path.with_suffix(".tmp")
    try:
        with connection() as source, closing(sqlite3.connect(temporary_path)) as target:
            source.backup(target)
        validate_database(temporary_path)
        os.replace(temporary_path, final_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()

    backups = sorted(
        destination.glob("pcims_*.db"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    cleanup_errors = []
    for old_backup in backups[keep:]:
        try:
            old_backup.unlink()
        except OSError as error:
            cleanup_errors.append(f"{old_backup}: {error}")
    return BackupResult(final_path, tuple(cleanup_errors))


def restore_backup(backup_path, pre_restore_directory=None):
    source_path = Path(backup_path).expanduser().resolve()
    live_path = get_database_path().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"Backup does not exist: {source_path}")
    if source_path == live_path:
        raise ValueError("The active database cannot be restored over itself.")
    validate_database(source_path)

    staged_path = live_path.with_name(
        f".{live_path.name}.{uuid.uuid4().hex}.restore.tmp"
    )
    try:
        shutil.copy2(source_path, staged_path)
        validate_database(staged_path)
        safety_backup = create_backup(pre_restore_directory)
        os.replace(staged_path, live_path)
    finally:
        if staged_path.exists():
            staged_path.unlink()
    return safety_backup
