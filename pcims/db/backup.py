"""Verified backup and atomic restore for the current PCIMS schema."""

import hashlib
import os
import shutil
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pcims.db.connection import Database, register_database_collations
from pcims.db.errors import DatabaseIntegrityError, SchemaVersionError
from pcims.db.schema import validate_current_data, validate_schema


@dataclass(frozen=True, slots=True)
class BackupResult(os.PathLike[str]):
    """A verified backup plus any non-fatal retention cleanup failures."""

    path: Path
    cleanup_errors: tuple[str, ...] = ()

    def __fspath__(self) -> str:
        return str(self.path)

    def __str__(self) -> str:
        return str(self.path)

    @property
    def has_cleanup_warnings(self) -> bool:
        return bool(self.cleanup_errors)

    @property
    def cleanup_warning(self) -> str:
        return "\n".join(self.cleanup_errors)


def _sync_file(path: Path) -> None:
    """Flush file contents before an atomic name replacement is reported durable."""
    with path.open("r+b") as file:
        os.fsync(file.fileno())


def _sync_directory(path: Path) -> None:
    """Flush a directory entry on platforms that expose directory descriptors."""
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _backup_prefix(database: Database) -> str:
    """Return a stable, non-identifying retention namespace for one database."""
    identity = os.path.normcase(str(database.path)).encode("utf-8")
    digest = hashlib.sha256(identity).hexdigest()[:12]
    return f"pcims_{digest}_"


def _remove_live_sidecars(database_path: Path) -> None:
    """Remove journals belonging to the old main file before atomic replacement."""
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{database_path}{suffix}")
        try:
            sidecar.unlink()
        except FileNotFoundError:
            pass


def _remove_temporary(path: Path, primary_error: BaseException | None) -> None:
    if not path.exists():
        return
    try:
        path.unlink()
    except OSError as cleanup_error:
        if primary_error is None:
            raise
        primary_error.add_note(
            f"Temporary file cleanup also failed for {path}: {cleanup_error}"
        )


def validate_database(path: str | os.PathLike[str]) -> None:
    resolved = Path(path).expanduser().resolve()
    with closing(sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True)) as database:
        register_database_collations(database)
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
        try:
            validate_schema(database)
            validate_current_data(database)
        except (DatabaseIntegrityError, SchemaVersionError) as error:
            raise sqlite3.DatabaseError(str(error)) from error


def _create_backup(
    destination_directory: str | os.PathLike[str] | None = None,
    keep: int = 14,
    *,
    database: Database,
) -> BackupResult:
    if keep < 1:
        raise ValueError("At least one backup must be retained.")
    destination = (
        Path(destination_directory or database.path.parent / "backups")
        .expanduser()
        .resolve()
    )
    destination.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).astimezone().strftime("%Y-%m-%d_%H-%M-%S_%f")
    prefix = _backup_prefix(database)
    final_path = destination / f"{prefix}{stamp}.db"
    temporary_path = final_path.with_suffix(".tmp")
    primary_error: BaseException | None = None
    publication_errors: list[str] = []
    try:
        with database.transaction() as source, closing(
            sqlite3.connect(temporary_path)
        ) as target:
            source.backup(target)
        validate_database(temporary_path)
        _sync_file(temporary_path)
        os.replace(temporary_path, final_path)
        try:
            _sync_directory(destination)
        except OSError as error:
            publication_errors.append(
                f"Backup was created, but its directory could not be flushed: {error}"
            )
    except BaseException as error:
        primary_error = error
        raise
    finally:
        _remove_temporary(temporary_path, primary_error)

    cleanup_errors = publication_errors
    try:
        backups = sorted(destination.glob(f"{prefix}*.db"), reverse=True)
    except OSError as error:
        cleanup_errors.append(
            f"Unable to inspect old backups in {destination}: {error}"
        )
        backups = []
    for old_backup in backups[keep:]:
        try:
            old_backup.unlink()
        except OSError as error:
            cleanup_errors.append(f"{old_backup}: {error}")
    return BackupResult(final_path, tuple(cleanup_errors))


def create_backup(
    destination_directory: str | os.PathLike[str] | None = None,
    keep: int = 14,
    *,
    database: Database,
) -> BackupResult:
    """Create and retain one backup without racing other maintenance."""
    with database.gate.maintenance():
        return _create_backup(destination_directory, keep, database=database)


def _restore_backup(
    backup_path: str | os.PathLike[str],
    pre_restore_directory: str | os.PathLike[str] | None = None,
    *,
    database: Database,
) -> BackupResult:
    source_path = Path(backup_path).expanduser().resolve()
    live_path = database.path
    if not source_path.is_file():
        raise FileNotFoundError(f"Backup does not exist: {source_path}")
    if source_path == live_path:
        raise ValueError("The active database cannot be restored over itself.")
    validate_database(source_path)

    staged_path = live_path.with_name(
        f".{live_path.name}.{uuid.uuid4().hex}.restore.tmp"
    )
    primary_error: BaseException | None = None
    try:
        shutil.copy2(source_path, staged_path)
        validate_database(staged_path)
        safety_backup = create_backup(pre_restore_directory, database=database)
        _sync_file(staged_path)
        _remove_live_sidecars(live_path)
        os.replace(staged_path, live_path)
        try:
            _sync_directory(live_path.parent)
        except OSError as error:
            safety_backup = BackupResult(
                safety_backup.path,
                (
                    *safety_backup.cleanup_errors,
                    f"Database was restored, but its directory could not be flushed: {error}",
                ),
            )
    except BaseException as error:
        primary_error = error
        raise
    finally:
        _remove_temporary(staged_path, primary_error)
    return safety_backup


def restore_backup(
    backup_path: str | os.PathLike[str],
    pre_restore_directory: str | os.PathLike[str] | None = None,
    *,
    database: Database,
) -> BackupResult:
    """Atomically replace the live database after all operations have drained."""
    with database.gate.maintenance(), database.gate.exclusive():
        return _restore_backup(backup_path, pre_restore_directory, database=database)
