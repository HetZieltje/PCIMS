"""Atomic purchase and expense write workflows."""

from collections.abc import Iterable

from pcims.db.command_support import normalized_command_text, unique_command_ids
from pcims.db.connection import Database
from pcims.db.errors import NotFoundError, ValidationError
from pcims.db.records import EXPENSE_SELECT, inserted_id
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
    placeholders = ",".join("?" for _ in ids)
    with database.transaction(write=True) as connection:
        rows = connection.execute(
            EXPENSE_SELECT + f" WHERE e.id IN ({placeholders})", ids
        ).fetchall()
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


def rename_expenses(
    expense_ids: Iterable[int], new_name: str, *, database: Database
) -> None:
    ids = unique_command_ids(expense_ids, "Expense ID")
    name = normalized_command_text(new_name, "New item name")
    placeholders = ",".join("?" for _ in ids)
    with database.transaction(write=True) as connection:
        count = connection.execute(
            # IDs are validated integers; only the number of placeholders varies.
            f"SELECT COUNT(*) FROM expenses WHERE id IN ({placeholders})",  # nosec B608
            ids,
        ).fetchone()[0]
        if count != len(ids):
            raise NotFoundError("One or more selected expenses no longer exist.")
        connection.execute(
            # IDs and name remain bound parameters; no user text enters the SQL.
            f"UPDATE expenses SET name=? WHERE id IN ({placeholders})",  # nosec B608
            [name, *ids],
        )
