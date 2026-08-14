"""Shared current-schema SQL fragments and row mapping."""

import sqlite3
from datetime import date
from typing import cast

from pcims.domain import ItemType
from pcims.models import Expense

EXPENSE_SELECT = """
    SELECT e.id,e.name,e.item_type,e.price_cents,e.purchase_date,
           p.id AS pc_id,p.name AS pc_name,si.sale_id
      FROM expenses e
      LEFT JOIN pc_parts pp ON pp.expense_id=e.id
      LEFT JOIN assembled_pcs p ON p.id=pp.pc_id
      LEFT JOIN sale_items si ON si.expense_id=e.id
"""


def expense_from_row(row: sqlite3.Row) -> Expense:
    return Expense(
        id=row["id"],
        name=row["name"],
        item_type=cast(ItemType, row["item_type"]),
        price_cents=row["price_cents"],
        purchase_date=date.fromisoformat(row["purchase_date"]),
        pc_id=row["pc_id"],
        pc_name=row["pc_name"],
        sale_id=row["sale_id"],
    )


def inserted_id(cursor: sqlite3.Cursor) -> int:
    row_id = cursor.lastrowid
    if row_id is None:
        raise sqlite3.DatabaseError("SQLite did not return an inserted row ID.")
    return row_id
