"""Atomic assembled-PC write workflows."""

from collections.abc import Iterable

from pcims.db.command_support import (
    bounded_cents_total,
    find_pc_name_collision,
    normalized_command_text,
    positive_command_id,
    unique_command_ids,
)
from pcims.db.connection import Database
from pcims.db.errors import NotFoundError, ValidationError
from pcims.db.records import EXPENSE_SELECT, inserted_id


def assemble_pc(
    name: str, expense_ids: Iterable[int], *, database: Database
) -> int:
    name = normalized_command_text(name, "PC name")
    ids = unique_command_ids(expense_ids, "Expense ID")
    placeholders = ",".join("?" for _ in ids)
    with database.transaction(write=True) as connection:
        collision = find_pc_name_collision(connection, name)
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
        bounded_cents_total(
            (row["price_cents"] for row in rows), "Combined PC cost"
        )
        pc_id = inserted_id(
            connection.execute("INSERT INTO assembled_pcs (name) VALUES (?)", (name,))
        )
        connection.executemany(
            "INSERT INTO pc_parts (pc_id,expense_id,position) VALUES (?,?,?)",
            ((pc_id, expense_id, position) for position, expense_id in enumerate(ids)),
        )
        return pc_id


def disassemble_pc(pc_id: int, *, database: Database) -> None:
    pc_id = positive_command_id(pc_id, "PC ID")
    with database.transaction(write=True) as connection:
        result = connection.execute("DELETE FROM assembled_pcs WHERE id=?", (pc_id,))
        if result.rowcount != 1:
            raise NotFoundError(f"PC {pc_id} does not exist.")


def rename_pc(pc_id: int, new_name: str, *, database: Database) -> None:
    pc_id = positive_command_id(pc_id, "PC ID")
    name = normalized_command_text(new_name, "New PC name")
    with database.transaction(write=True) as connection:
        collision = find_pc_name_collision(connection, name, exclude_id=pc_id)
        if collision:
            raise ValidationError(f"A PC named '{collision['name']}' already exists.")
        result = connection.execute(
            "UPDATE assembled_pcs SET name=? WHERE id=?", (name, pc_id)
        )
        if result.rowcount != 1:
            raise NotFoundError(f"PC {pc_id} does not exist.")
