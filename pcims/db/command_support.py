"""Shared normalization and invariant helpers for write workflows."""

import sqlite3
from collections.abc import Iterable
from typing import cast

from pcims.db.errors import ValidationError
from pcims.db.records import EXPENSE_SELECT
from pcims.domain import normalized_id, normalized_ids, normalized_text
from pcims.money import MAX_MONEY_CENTS

SQLITE_ID_BATCH_SIZE = 500


def normalized_command_text(value: object, label: str) -> str:
    try:
        return normalized_text(value, label)
    except ValueError as error:
        raise ValidationError(str(error)) from error


def positive_command_id(value: object, label: str = "ID") -> int:
    try:
        return normalized_id(value, label)
    except ValueError as error:
        raise ValidationError(str(error)) from error


def unique_command_ids(values: Iterable[object], label: str) -> list[int]:
    try:
        return list(normalized_ids(values, label))
    except ValueError as error:
        raise ValidationError(str(error)) from error


def bounded_cents_total(values: Iterable[int], label: str) -> int:
    total = sum(values)
    if total > MAX_MONEY_CENTS:
        raise ValidationError(f"{label} is too large.")
    return total


def id_batches(identifiers: list[int]) -> Iterable[list[int]]:
    """Keep dynamically bound ID groups below SQLite's portable limit."""
    for offset in range(0, len(identifiers), SQLITE_ID_BATCH_SIZE):
        yield identifiers[offset : offset + SQLITE_ID_BATCH_SIZE]


def select_expense_rows(
    connection: sqlite3.Connection, identifiers: list[int]
) -> list[sqlite3.Row]:
    rows: list[sqlite3.Row] = []
    for batch in id_batches(identifiers):
        placeholders = ",".join("?" for _ in batch)
        rows.extend(
            connection.execute(
                # Only the validated batch width changes the SQL structure.
                EXPENSE_SELECT + f" WHERE e.id IN ({placeholders})",  # nosec B608
                batch,
            )
        )
    return rows


def find_pc_name_collision(
    connection: sqlite3.Connection,
    name: str,
    exclude_id: int | None = None,
) -> sqlite3.Row | None:
    if exclude_id is None:
        return cast(
            sqlite3.Row | None,
            connection.execute(
                "SELECT id,name FROM pcs WHERE name=?", (name,)
            ).fetchone(),
        )
    return cast(
        sqlite3.Row | None,
        connection.execute(
            "SELECT id,name FROM pcs WHERE name=? AND id<>?",
            (name, exclude_id),
        ).fetchone(),
    )
