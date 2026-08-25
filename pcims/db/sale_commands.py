"""Atomic sale and sale-reversal write workflows."""

import sqlite3
from collections.abc import Iterable

from pcims.db.command_support import (
    bounded_cents_total,
    positive_command_id,
    select_expense_rows,
    unique_command_ids,
)
from pcims.db.connection import Database
from pcims.db.errors import NotFoundError, ValidationError
from pcims.db.records import EXPENSE_SELECT, inserted_id
from pcims.domain import SaleTerms


def _validate_sale_date(rows: Iterable[sqlite3.Row], sale_day: str) -> None:
    for row in rows:
        if sale_day < row["purchase_date"]:
            raise ValidationError(
                f"Sale date cannot be before purchase date {row['purchase_date']}."
            )


def sell_items(
    expense_ids: Iterable[int], terms: SaleTerms, *, database: Database
) -> int:
    ids = unique_command_ids(expense_ids, "Item ID")
    sale_day = terms.sale_date.isoformat()
    with database.transaction(write=True) as connection:
        rows = sorted(select_expense_rows(connection, ids), key=lambda row: row["id"])
        if len(rows) != len(ids):
            raise NotFoundError("One or more selected items no longer exist.")
        for row in rows:
            if row["pc_id"] is not None or row["sale_id"] is not None:
                raise ValidationError(f"'{row['name']}' is not available for sale.")
        _validate_sale_date(rows, sale_day)
        names = {row["name"] for row in rows}
        name = rows[0]["name"] if len(names) == 1 else f"{len(rows)} items"
        bounded_cents_total((row["price_cents"] for row in rows), "Combined item cost")
        sale_id = inserted_id(
            connection.execute(
                "INSERT INTO sales "
                "(name,kind,pc_id,selling_price_cents,sale_date) "
                "VALUES (?,'item',NULL,?,?)",
                (name, terms.selling_price_cents, sale_day),
            )
        )
        connection.executemany(
            "INSERT INTO sale_items (sale_id,item_id,position) VALUES (?,?,?)",
            ((sale_id, item_id, position) for position, item_id in enumerate(ids)),
        )
        return sale_id


def sell_pc(pc_id: int, terms: SaleTerms, *, database: Database) -> int:
    pc_id = positive_command_id(pc_id, "PC ID")
    sale_day = terms.sale_date.isoformat()
    with database.transaction(write=True) as connection:
        pc = connection.execute(
            "SELECT id,name,status FROM pcs WHERE id=?", (pc_id,)
        ).fetchone()
        if pc is None:
            raise NotFoundError(f"PC {pc_id} does not exist.")
        if pc["status"] != "active":
            raise ValidationError(f"PC '{pc['name']}' has already been sold.")
        rows = connection.execute(
            EXPENSE_SELECT + " WHERE p.id=? ORDER BY pp.position", (pc_id,)
        ).fetchall()
        if not rows:
            raise ValidationError(f"PC '{pc['name']}' has no components.")
        _validate_sale_date(rows, sale_day)
        bounded_cents_total((row["price_cents"] for row in rows), "Combined PC cost")
        item_ids = [row["id"] for row in rows]
        sale_id = inserted_id(
            connection.execute(
                "INSERT INTO sales "
                "(name,kind,pc_id,selling_price_cents,sale_date) "
                "VALUES (?,'pc',?,?,?)",
                (pc["name"], pc_id, terms.selling_price_cents, sale_day),
            )
        )
        connection.executemany(
            "INSERT INTO sale_items (sale_id,item_id,position) VALUES (?,?,?)",
            ((sale_id, item_id, position) for position, item_id in enumerate(item_ids)),
        )
        return sale_id


def update_sale(sale_id: int, terms: SaleTerms, *, database: Database) -> None:
    """Correct the editable sale terms without changing sold-item membership."""
    sale_id = positive_command_id(sale_id, "Sale ID")
    sale_day = terms.sale_date.isoformat()
    with database.transaction(write=True) as connection:
        sale = connection.execute(
            "SELECT id FROM sales WHERE id=?", (sale_id,)
        ).fetchone()
        if sale is None:
            raise NotFoundError(f"Sale {sale_id} does not exist.")
        rows = connection.execute(
            """SELECT i.purchase_date FROM sale_items si
               JOIN inventory_items i ON i.id=si.item_id
               WHERE si.sale_id=?""",
            (sale_id,),
        ).fetchall()
        if not rows:
            raise ValidationError(f"Sale {sale_id} contains no items.")
        _validate_sale_date(rows, sale_day)
        result = connection.execute(
            """UPDATE sales SET selling_price_cents=?,sale_date=? WHERE id=?""",
            (terms.selling_price_cents, sale_day, sale_id),
        )
        if result.rowcount != 1:
            raise NotFoundError(f"Sale {sale_id} does not exist.")


def undo_sale(sale_id: int, *, database: Database) -> None:
    sale_id = positive_command_id(sale_id, "Sale ID")
    with database.transaction(write=True) as connection:
        sale = connection.execute(
            "SELECT id,name,kind,pc_id FROM sales WHERE id=?", (sale_id,)
        ).fetchone()
        if sale is None:
            raise NotFoundError(f"Sale {sale_id} does not exist.")
        item_ids = [
            row[0]
            for row in connection.execute(
                "SELECT item_id FROM sale_items WHERE sale_id=? ORDER BY position",
                (sale_id,),
            )
        ]
        if not item_ids:
            raise ValidationError(f"Sale {sale_id} contains no recoverable items.")
        connection.execute("DELETE FROM sales WHERE id=?", (sale_id,))
