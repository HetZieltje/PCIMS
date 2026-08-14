"""Shared normalization and invariant helpers for write workflows."""

import sqlite3
from collections.abc import Iterable
from typing import cast

from pcims.db.errors import ValidationError
from pcims.domain import normalized_id, normalized_ids, normalized_text
from pcims.money import MAX_MONEY_CENTS


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


def find_pc_name_collision(
    connection: sqlite3.Connection,
    name: str,
    exclude_id: int | None = None,
) -> sqlite3.Row | None:
    if exclude_id is None:
        return cast(
            sqlite3.Row | None,
            connection.execute(
                "SELECT id,name FROM assembled_pcs WHERE name=?", (name,)
            ).fetchone(),
        )
    return cast(
        sqlite3.Row | None,
        connection.execute(
            "SELECT id,name FROM assembled_pcs WHERE name=? AND id<>?",
            (name, exclude_id),
        ).fetchone(),
    )
