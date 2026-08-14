"""Atomic state-changing workflows over the current PCIMS schema."""

import sqlite3
from collections.abc import Iterable

from pcims.db.connection import Database
from pcims.db.errors import NotFoundError, ValidationError
from pcims.db.records import EXPENSE_SELECT, inserted_id
from pcims.domain import (
    NewExpense,
    SaleTerms,
    normalized_id,
    normalized_ids,
    normalized_text,
)
from pcims.money import MAX_MONEY_CENTS


def _text(value: object, label: str) -> str:
    try:
        return normalized_text(value, label)
    except ValueError as error:
        raise ValidationError(str(error)) from error


def _positive_id(value: object, label: str = "ID") -> int:
    try:
        return normalized_id(value, label)
    except ValueError as error:
        raise ValidationError(str(error)) from error


def _unique_ids(values: Iterable[object], label: str) -> list[int]:
    try:
        return list(normalized_ids(values, label))
    except ValueError as error:
        raise ValidationError(str(error)) from error


def _bounded_cents_total(values: Iterable[int], label: str) -> int:
    total = sum(values)
    if total > MAX_MONEY_CENTS:
        raise ValidationError(f"{label} is too large.")
    return total


def _find_pc_name_collision(
    connection: sqlite3.Connection, name: str, exclude_id: int | None = None
) -> sqlite3.Row | None:
    folded = name.casefold()
    return next(
        (
            row
            for row in connection.execute("SELECT id,name FROM assembled_pcs")
            if row["id"] != exclude_id and row["name"].casefold() == folded
        ),
        None,
    )


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
    ids = _unique_ids(expense_ids, "Expense ID")
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
    ids = _unique_ids(expense_ids, "Expense ID")
    new_name = _text(new_name, "New item name")
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
            [new_name, *ids],
        )


def assemble_pc(
    name: str, expense_ids: Iterable[int], *, database: Database
) -> int:
    name = _text(name, "PC name")
    ids = _unique_ids(expense_ids, "Expense ID")
    placeholders = ",".join("?" for _ in ids)
    with database.transaction(write=True) as connection:
        collision = _find_pc_name_collision(connection, name)
        if collision:
            raise ValidationError(f"A PC named '{collision['name']}' already exists.")
        rows = connection.execute(
            EXPENSE_SELECT + f" WHERE e.id IN ({placeholders})", ids
        ).fetchall()
        if len(rows) != len(ids):
            raise NotFoundError("One or more selected expenses no longer exist.")
        for row in rows:
            if row["pc_id"] is not None or row["sale_id"] is not None:
                raise ValidationError(f"'{row['name']}' is not available for assembly.")
        pc_id = inserted_id(
            connection.execute("INSERT INTO assembled_pcs (name) VALUES (?)", (name,))
        )
        connection.executemany(
            "INSERT INTO pc_parts (pc_id,expense_id,position) VALUES (?,?,?)",
            ((pc_id, expense_id, position) for position, expense_id in enumerate(ids)),
        )
        return pc_id


def disassemble_pc(pc_id: int, *, database: Database) -> None:
    pc_id = _positive_id(pc_id, "PC ID")
    with database.transaction(write=True) as connection:
        result = connection.execute("DELETE FROM assembled_pcs WHERE id=?", (pc_id,))
        if result.rowcount != 1:
            raise NotFoundError(f"PC {pc_id} does not exist.")


def rename_pc(pc_id: int, new_name: str, *, database: Database) -> None:
    pc_id = _positive_id(pc_id, "PC ID")
    new_name = _text(new_name, "New PC name")
    with database.transaction(write=True) as connection:
        collision = _find_pc_name_collision(connection, new_name, exclude_id=pc_id)
        if collision:
            raise ValidationError(f"A PC named '{collision['name']}' already exists.")
        result = connection.execute(
            "UPDATE assembled_pcs SET name=? WHERE id=?", (new_name, pc_id)
        )
        if result.rowcount != 1:
            raise NotFoundError(f"PC {pc_id} does not exist.")


def _validate_sale_date(rows: Iterable[sqlite3.Row], sale_day: str) -> None:
    for row in rows:
        if sale_day < row["purchase_date"]:
            raise ValidationError(
                f"Sale date cannot be before purchase date {row['purchase_date']}."
            )


def sell_items(
    expense_ids: Iterable[int], terms: SaleTerms, *, database: Database
) -> int:
    ids = _unique_ids(expense_ids, "Expense ID")
    sale_day = terms.sale_date.isoformat()
    placeholders = ",".join("?" for _ in ids)
    with database.transaction(write=True) as connection:
        rows = connection.execute(
            EXPENSE_SELECT + f" WHERE e.id IN ({placeholders}) ORDER BY e.id", ids
        ).fetchall()
        if len(rows) != len(ids):
            raise NotFoundError("One or more selected expenses no longer exist.")
        for row in rows:
            if row["pc_id"] is not None or row["sale_id"] is not None:
                raise ValidationError(f"'{row['name']}' is not available for sale.")
        _validate_sale_date(rows, sale_day)
        names = {row["name"] for row in rows}
        name = rows[0]["name"] if len(names) == 1 else f"{len(rows)} items"
        cost_cents = _bounded_cents_total(
            (row["price_cents"] for row in rows), "Combined item cost"
        )
        sale_id = inserted_id(
            connection.execute(
                "INSERT INTO sales "
                "(name,kind,cost_cents,selling_price_cents,sale_date) "
                "VALUES (?,'item',?,?,?)",
                (name, cost_cents, terms.selling_price_cents, sale_day),
            )
        )
        connection.executemany(
            "INSERT INTO sale_items (sale_id,expense_id,position) VALUES (?,?,?)",
            (
                (sale_id, expense_id, position)
                for position, expense_id in enumerate(ids)
            ),
        )
        return sale_id


def sell_pc(pc_id: int, terms: SaleTerms, *, database: Database) -> int:
    pc_id = _positive_id(pc_id, "PC ID")
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
        cost_cents = _bounded_cents_total(
            (row["price_cents"] for row in rows), "Combined PC cost"
        )
        expense_ids = [row["id"] for row in rows]
        connection.execute("DELETE FROM assembled_pcs WHERE id=?", (pc_id,))
        sale_id = inserted_id(
            connection.execute(
                "INSERT INTO sales "
                "(name,kind,cost_cents,selling_price_cents,sale_date) "
                "VALUES (?,'pc',?,?,?)",
                (pc["name"], cost_cents, terms.selling_price_cents, sale_day),
            )
        )
        connection.executemany(
            "INSERT INTO sale_items (sale_id,expense_id,position) VALUES (?,?,?)",
            (
                (sale_id, expense_id, position)
                for position, expense_id in enumerate(expense_ids)
            ),
        )
        return sale_id


def undo_sale(sale_id: int, *, database: Database) -> None:
    sale_id = _positive_id(sale_id, "Sale ID")
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
            collision = _find_pc_name_collision(connection, sale["name"])
            if collision:
                raise ValidationError(
                    "Cannot undo while an assembled PC named "
                    f"'{collision['name']}' exists."
                )
            connection.execute("DELETE FROM sales WHERE id=?", (sale_id,))
            pc_id = inserted_id(
                connection.execute(
                    "INSERT INTO assembled_pcs (name) VALUES (?)", (sale["name"],)
                )
            )
            connection.executemany(
                "INSERT INTO pc_parts (pc_id,expense_id,position) VALUES (?,?,?)",
                (
                    (pc_id, expense_id, position)
                    for position, expense_id in enumerate(item_ids)
                ),
            )
        else:
            connection.execute("DELETE FROM sales WHERE id=?", (sale_id,))
