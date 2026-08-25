"""Atomic sale and sale-reversal write workflows."""

import sqlite3
from collections.abc import Iterable

from pcims.db.audit import record_audit_event
from pcims.db.command_support import (
    bounded_cents_total,
    find_pc_name_collision,
    next_record_id,
    positive_command_id,
    select_expense_rows,
    unique_command_ids,
)
from pcims.db.connection import Database
from pcims.db.errors import NotFoundError, ValidationError
from pcims.db.records import EXPENSE_SELECT
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
    ids = unique_command_ids(expense_ids, "Expense ID")
    sale_day = terms.sale_date.isoformat()
    with database.transaction(write=True) as connection:
        rows = sorted(select_expense_rows(connection, ids), key=lambda row: row["id"])
        if len(rows) != len(ids):
            raise NotFoundError("One or more selected expenses no longer exist.")
        for row in rows:
            if row["pc_id"] is not None or row["sale_id"] is not None:
                raise ValidationError(f"'{row['name']}' is not available for sale.")
        _validate_sale_date(rows, sale_day)
        names = {row["name"] for row in rows}
        name = rows[0]["name"] if len(names) == 1 else f"{len(rows)} items"
        bounded_cents_total((row["price_cents"] for row in rows), "Combined item cost")
        sale_id = next_record_id(connection, "sales")
        connection.executemany(
            "INSERT INTO sale_items (sale_id,expense_id,position) VALUES (?,?,?)",
            (
                (sale_id, expense_id, position)
                for position, expense_id in enumerate(ids)
            ),
        )
        connection.execute(
            "INSERT INTO sales "
            "(id,name,kind,selling_price_cents,sale_date) "
            "VALUES (?,?,'item',?,?)",
            (sale_id, name, terms.selling_price_cents, sale_day),
        )
        record_audit_event(
            connection,
            "sold",
            "sale",
            sale_id,
            f"Sold {len(ids)} item(s) as '{name}'.",
        )
        return sale_id


def sell_pc(pc_id: int, terms: SaleTerms, *, database: Database) -> int:
    pc_id = positive_command_id(pc_id, "PC ID")
    sale_day = terms.sale_date.isoformat()
    with database.transaction(write=True) as connection:
        pc = connection.execute(
            "SELECT id,name FROM assembled_pcs WHERE id=?", (pc_id,)
        ).fetchone()
        if pc is None:
            raise NotFoundError(f"PC {pc_id} does not exist.")
        rows = connection.execute(
            EXPENSE_SELECT + " WHERE p.id=? ORDER BY pp.position", (pc_id,)
        ).fetchall()
        if not rows:
            raise ValidationError(f"PC '{pc['name']}' has no components.")
        _validate_sale_date(rows, sale_day)
        bounded_cents_total((row["price_cents"] for row in rows), "Combined PC cost")
        expense_ids = [row["id"] for row in rows]
        connection.execute("DELETE FROM assembled_pcs WHERE id=?", (pc_id,))
        sale_id = next_record_id(connection, "sales")
        connection.executemany(
            "INSERT INTO sale_items (sale_id,expense_id,position) VALUES (?,?,?)",
            (
                (sale_id, expense_id, position)
                for position, expense_id in enumerate(expense_ids)
            ),
        )
        connection.execute(
            "INSERT INTO sales "
            "(id,name,kind,selling_price_cents,sale_date) "
            "VALUES (?,?,'pc',?,?)",
            (sale_id, pc["name"], terms.selling_price_cents, sale_day),
        )
        record_audit_event(
            connection,
            "sold",
            "sale",
            sale_id,
            f"Sold PC '{pc['name']}'.",
        )
        return sale_id


def undo_sale(sale_id: int, *, database: Database) -> None:
    sale_id = positive_command_id(sale_id, "Sale ID")
    with database.transaction(write=True) as connection:
        sale = connection.execute(
            "SELECT id,name,kind FROM sales WHERE id=?", (sale_id,)
        ).fetchone()
        if sale is None:
            raise NotFoundError(f"Sale {sale_id} does not exist.")
        item_ids = [
            row[0]
            for row in connection.execute(
                "SELECT expense_id FROM sale_items WHERE sale_id=? ORDER BY position",
                (sale_id,),
            )
        ]
        if not item_ids:
            raise ValidationError(f"Sale {sale_id} contains no recoverable items.")
        if sale["kind"] == "pc":
            collision = find_pc_name_collision(connection, sale["name"])
            if collision:
                raise ValidationError(
                    "Cannot undo while an assembled PC named "
                    f"'{collision['name']}' exists."
                )
            connection.execute("DELETE FROM sales WHERE id=?", (sale_id,))
            pc_id = next_record_id(connection, "assembled_pcs")
            connection.executemany(
                "INSERT INTO pc_parts (pc_id,expense_id,position) VALUES (?,?,?)",
                (
                    (pc_id, expense_id, position)
                    for position, expense_id in enumerate(item_ids)
                ),
            )
            connection.execute(
                "INSERT INTO assembled_pcs (id,name) VALUES (?,?)",
                (pc_id, sale["name"]),
            )
        else:
            connection.execute("DELETE FROM sales WHERE id=?", (sale_id,))
        record_audit_event(
            connection,
            "sale_undone",
            "sale",
            sale_id,
            f"Undid sale '{sale['name']}'.",
        )
