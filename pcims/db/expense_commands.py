"""Atomic purchase and expense write workflows."""

from collections.abc import Iterable

from pcims.db.command_support import (
    bounded_cents_total,
    positive_command_id,
    select_expense_rows,
    unique_command_ids,
)
from pcims.db.connection import Database
from pcims.db.errors import DatabaseIntegrityError, NotFoundError, ValidationError
from pcims.db.records import inserted_id
from pcims.domain import NewExpense


def add_expenses(items: Iterable[NewExpense], *, database: Database) -> list[int]:
    """Record one or more purchased items atomically."""
    expenses = tuple(items)
    if not expenses:
        raise ValidationError("At least one purchase item is required.")
    with database.transaction(write=True) as connection:
        return [
            inserted_id(
                connection.execute(
                    "INSERT INTO expenses "
                    "(name,item_type,price_cents,purchase_date) VALUES (?,?,?,?)",
                    (
                        expense.name,
                        expense.item_type,
                        expense.price_cents,
                        expense.purchase_date.isoformat(),
                    ),
                )
            )
            for expense in expenses
        ]


def delete_expenses(expense_ids: Iterable[int], *, database: Database) -> None:
    ids = unique_command_ids(expense_ids, "Expense ID")
    with database.transaction(write=True) as connection:
        rows = select_expense_rows(connection, ids)
        if len(rows) != len(ids):
            found = {row["id"] for row in rows}
            missing = next(item_id for item_id in ids if item_id not in found)
            raise NotFoundError(f"Expense {missing} does not exist.")
        for row in rows:
            if row["pc_id"] is not None:
                raise ValidationError(
                    f"Expense {row['id']} belongs to PC '{row['pc_name']}'."
                )
            if row["sale_id"] is not None:
                raise ValidationError(
                    f"Expense {row['id']} has sale history. Undo the sale first."
                )
        connection.executemany(
            "DELETE FROM expenses WHERE id=?", ((item_id,) for item_id in ids)
        )


def update_expense(
    expense_id: int,
    replacement: NewExpense,
    *,
    database: Database,
) -> None:
    """Replace every editable field while preserving the expense identity."""
    expense_id = positive_command_id(expense_id, "Expense ID")
    with database.transaction(write=True) as connection:
        rows = select_expense_rows(connection, [expense_id])
        if not rows:
            raise NotFoundError(f"Expense {expense_id} does not exist.")
        row = rows[0]
        if row["sale_id"] is not None:
            raise ValidationError(
                f"Expense {expense_id} has sale history and cannot be edited."
            )

        pc_id = int(row["pc_id"]) if row["pc_id"] is not None else None
        pc_name = str(row["pc_name"]) if row["pc_name"] is not None else None
        pc_expense_ids: tuple[int, ...] = ()
        if pc_id is not None:
            if pc_name is None:
                raise DatabaseIntegrityError(
                    f"Expense {expense_id} has an invalid PC relationship."
                )
            membership = connection.execute(
                """SELECT pp.expense_id,e.price_cents
                     FROM pc_parts pp JOIN expenses e ON e.id=pp.expense_id
                    WHERE pp.pc_id=? ORDER BY pp.position""",
                (pc_id,),
            ).fetchall()
            pc_expense_ids = tuple(int(part["expense_id"]) for part in membership)
            bounded_cents_total(
                (
                    replacement.price_cents
                    if int(part["expense_id"]) == expense_id
                    else int(part["price_cents"])
                    for part in membership
                ),
                "Combined PC cost",
            )
            connection.execute("DELETE FROM assembled_pcs WHERE id=?", (pc_id,))

        result = connection.execute(
            """UPDATE expenses
                  SET name=?,item_type=?,price_cents=?,purchase_date=?
                WHERE id=?""",
            (
                replacement.name,
                replacement.item_type,
                replacement.price_cents,
                replacement.purchase_date.isoformat(),
                expense_id,
            ),
        )
        if result.rowcount != 1:
            raise NotFoundError(f"Expense {expense_id} does not exist.")

        if pc_id is not None:
            connection.executemany(
                "INSERT INTO pc_parts (pc_id,expense_id,position) VALUES (?,?,?)",
                (
                    (pc_id, part_id, position)
                    for position, part_id in enumerate(pc_expense_ids)
                ),
            )
            connection.execute(
                "INSERT INTO assembled_pcs (id,name) VALUES (?,?)", (pc_id, pc_name)
            )
