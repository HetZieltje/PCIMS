"""Atomic assembled-PC write workflows."""

from collections.abc import Iterable

from pcims.db.command_support import (
    bounded_cents_total,
    find_pc_name_collision,
    next_record_id,
    normalized_command_text,
    positive_command_id,
    select_expense_rows,
    unique_command_ids,
)
from pcims.db.connection import Database
from pcims.db.errors import NotFoundError, ValidationError


def assemble_pc(name: str, expense_ids: Iterable[int], *, database: Database) -> int:
    name = normalized_command_text(name, "PC name")
    ids = unique_command_ids(expense_ids, "Expense ID")
    with database.transaction(write=True) as connection:
        collision = find_pc_name_collision(connection, name)
        if collision:
            raise ValidationError(f"A PC named '{collision['name']}' already exists.")
        rows = select_expense_rows(connection, ids)
        if len(rows) != len(ids):
            raise NotFoundError("One or more selected expenses no longer exist.")
        for row in rows:
            if row["pc_id"] is not None or row["sale_id"] is not None:
                raise ValidationError(f"'{row['name']}' is not available for assembly.")
        bounded_cents_total((row["price_cents"] for row in rows), "Combined PC cost")
        pc_id = next_record_id(connection, "assembled_pcs")
        connection.executemany(
            "INSERT INTO pc_parts (pc_id,expense_id,position) VALUES (?,?,?)",
            ((pc_id, expense_id, position) for position, expense_id in enumerate(ids)),
        )
        connection.execute(
            "INSERT INTO assembled_pcs (id,name) VALUES (?,?)", (pc_id, name)
        )
        return pc_id


def disassemble_pc(pc_id: int, *, database: Database) -> None:
    pc_id = positive_command_id(pc_id, "PC ID")
    with database.transaction(write=True) as connection:
        result = connection.execute("DELETE FROM assembled_pcs WHERE id=?", (pc_id,))
        if result.rowcount != 1:
            raise NotFoundError(f"PC {pc_id} does not exist.")


def update_pc(
    pc_id: int,
    new_name: str,
    expense_ids: Iterable[int],
    *,
    database: Database,
) -> None:
    """Replace a PC name and ordered membership atomically, retaining its ID."""
    pc_id = positive_command_id(pc_id, "PC ID")
    name = normalized_command_text(new_name, "New PC name")
    ids = unique_command_ids(expense_ids, "Expense ID")
    with database.transaction(write=True) as connection:
        pc = connection.execute(
            "SELECT id FROM assembled_pcs WHERE id=?", (pc_id,)
        ).fetchone()
        if pc is None:
            raise NotFoundError(f"PC {pc_id} does not exist.")
        collision = find_pc_name_collision(connection, name, exclude_id=pc_id)
        if collision:
            raise ValidationError(f"A PC named '{collision['name']}' already exists.")
        rows = select_expense_rows(connection, ids)
        if len(rows) != len(ids):
            raise NotFoundError("One or more selected expenses no longer exist.")
        rows_by_id = {int(row["id"]): row for row in rows}
        for expense_id in ids:
            row = rows_by_id[expense_id]
            assigned_pc_id = row["pc_id"]
            if row["sale_id"] is not None:
                raise ValidationError(f"'{row['name']}' has already been sold.")
            if assigned_pc_id is not None and int(assigned_pc_id) != pc_id:
                raise ValidationError(
                    f"'{row['name']}' belongs to PC '{row['pc_name']}'."
                )
        bounded_cents_total(
            (int(rows_by_id[expense_id]["price_cents"]) for expense_id in ids),
            "Combined PC cost",
        )
        connection.execute("DELETE FROM assembled_pcs WHERE id=?", (pc_id,))
        connection.executemany(
            "INSERT INTO pc_parts (pc_id,expense_id,position) VALUES (?,?,?)",
            ((pc_id, expense_id, position) for position, expense_id in enumerate(ids)),
        )
        connection.execute(
            "INSERT INTO assembled_pcs (id,name) VALUES (?,?)", (pc_id, name)
        )
