"""Persistence adapters for the canonical inventory lifecycle."""

import sqlite3

from pcims.db.errors import ValidationError
from pcims.lifecycle import (
    InventoryState,
    ItemPlacement,
    LifecycleEvent,
    require_transition,
)


def placement_from_row(row: sqlite3.Row) -> ItemPlacement:
    try:
        return ItemPlacement(
            pc_id=row["pc_id"],
            laptop_id=row["laptop_id"],
            is_laptop=bool(row["is_laptop"]),
            sale_id=row["sale_id"],
        )
    except ValueError as error:
        raise ValidationError(str(error)) from error


def require_row_transition(
    row: sqlite3.Row,
    event: LifecycleEvent,
    target: InventoryState,
) -> None:
    try:
        require_transition(
            event,
            placement_from_row(row).state,
            target,
            item_name=str(row["name"]),
        )
    except ValueError as error:
        raise ValidationError(str(error)) from error
