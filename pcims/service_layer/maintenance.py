"""Backup, restore, storage, and export application services."""

import os
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from pcims.contracts import (
    BackupResult,
    DiagnosticCheck,
    DiagnosticsSnapshot,
    RestoreResult,
    StorageSummary,
)
from pcims.db.connection import Database
from pcims.db.schema import (
    initialize_database,
    validate_current_data,
    validate_proof_files,
    validate_schema,
)
from pcims.diagnostics import log_path, read_log_tail, startup_stages


class MaintenanceServices:
    database: Database

    def export_csv(self, directory: str | os.PathLike[str]) -> tuple[Path, Path]:
        from pcims.db.export import export_csv

        return export_csv(directory, database=self.database)

    def create_backup(
        self,
        destination_directory: str | os.PathLike[str] | None = None,
        keep: int = 14,
    ) -> BackupResult:
        from pcims.db.backup import create_backup

        return create_backup(destination_directory, keep, database=self.database)

    def storage_summary(self) -> StorageSummary:
        from pcims.db.backup import backup_usage

        with self.database.transaction() as connection:
            proof_count, proof_bytes = connection.execute(
                "SELECT COUNT(*),COALESCE(SUM(length(content)),0) FROM proof_files"
            ).fetchone()
        database_bytes = 0
        for path in (
            self.database.path,
            Path(f"{self.database.path}-wal"),
            Path(f"{self.database.path}-shm"),
        ):
            try:
                database_bytes += path.stat().st_size
            except OSError:
                continue
        backup_count, backup_bytes = backup_usage(database=self.database)
        return StorageSummary(
            database_bytes=database_bytes,
            proof_bytes=int(proof_bytes),
            proof_count=int(proof_count),
            backup_bytes=backup_bytes,
            backup_count=backup_count,
        )

    def diagnostics_snapshot(self, thorough: bool = False) -> DiagnosticsSnapshot:
        checks: list[DiagnosticCheck] = []

        def check(name: str, operation: Callable[[], str]) -> None:
            started = perf_counter()
            try:
                detail = operation()
                status = "Passed"
            except (OSError, sqlite3.DatabaseError, RuntimeError, ValueError) as error:
                detail = str(error)
                status = "Failed"
            checks.append(
                DiagnosticCheck(
                    len(checks) + 1,
                    name,
                    status,
                    detail,
                    round((perf_counter() - started) * 1000),
                )
            )

        with self.database.transaction() as connection:

            def storage_check() -> str:
                result = str(
                    connection.execute(
                        "PRAGMA integrity_check" if thorough else "PRAGMA quick_check"
                    ).fetchone()[0]
                )
                if result != "ok":
                    raise sqlite3.DatabaseError(result)
                violation = connection.execute("PRAGMA foreign_key_check").fetchone()
                if violation is not None:
                    raise sqlite3.DatabaseError(
                        f"foreign-key violation in {violation[0]} row {violation[1]}"
                    )
                return "SQLite storage and foreign keys are valid."

            check("Database storage", storage_check)
            check(
                "Schema",
                lambda: _validated_detail(
                    lambda: validate_schema(connection), "Schema v4 is current."
                ),
            )
            check(
                "Inventory rules",
                lambda: _validated_detail(
                    lambda: validate_current_data(connection),
                    "Inventory, PC, laptop, and sale relationships are consistent.",
                ),
            )
            if thorough:
                check(
                    "Proof contents",
                    lambda: _validated_detail(
                        lambda: validate_proof_files(connection),
                        "Every proof signature and SHA-256 hash is valid.",
                    ),
                )
            else:
                proof_count, proof_bytes = connection.execute(
                    "SELECT COUNT(*),COALESCE(SUM(length(content)),0) FROM proof_files"
                ).fetchone()
                checks.append(
                    DiagnosticCheck(
                        len(checks) + 1,
                        "Proof storage",
                        "Not fully checked",
                        f"{int(proof_count)} file(s), {int(proof_bytes):,} bytes. "
                        "Run the full check to verify content hashes.",
                        0,
                    )
                )

        storage = self.storage_summary()
        checks.append(
            DiagnosticCheck(
                len(checks) + 1,
                "Automatic backups",
                "Passed" if storage.backup_count else "Warning",
                f"{storage.backup_count} retained backup(s), "
                f"{storage.backup_bytes:,} bytes.",
                0,
            )
        )
        return DiagnosticsSnapshot(
            generated_at=datetime.now(UTC).astimezone(),
            checks=tuple(checks),
            storage=storage,
            startup=startup_stages(),
            log_tail=_combined_log_tail(self.database.path.parent),
        )

    def restore_backup(
        self,
        backup_path: str | os.PathLike[str],
        pre_restore_directory: str | os.PathLike[str] | None = None,
        keep: int = 14,
    ) -> RestoreResult:
        from pcims.db.backup import restore_backup

        result = restore_backup(
            backup_path, pre_restore_directory, keep, database=self.database
        )
        initialize_database(self.database)
        return result

    @property
    def database_path(self) -> Path:
        return self.database.path


def _validated_detail(operation: Callable[[], None], detail: str) -> str:
    operation()
    return detail


def _combined_log_tail(data_directory: Path) -> str:
    sections: list[str] = []
    for title, path in (
        ("Application", log_path(data_directory)),
        ("Unexpected errors", data_directory / "pcims-errors.log"),
    ):
        content = read_log_tail(path)
        if not content.startswith("No application log"):
            sections.append(f"{title}:\n{content}")
    return "\n\n".join(sections) or "No application log has been written yet."
