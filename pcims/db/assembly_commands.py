"""Atomic assembled-PC write workflows."""

from collections.abc import Iterable

from pcims.db.audit import record_audit_event
from pcims.db.command_support import (
    bounded_cents_total,
    find_pc_name_collision,
    normalized_command_text,
    positive_command_id,
    select_expense_rows,
    unique_command_ids,
)
from pcims.db.connection import Database
from pcims.db.errors import NotFoundError, ValidationError
from pcims.db.records import inserted_id


def assemble_pc(name: str, expense_ids: Iterable[int], *, database: Database) -> int:
    name = normalized_command_text(name, "PC name")
    ids = unique_command_ids(expense_ids, "Item ID")
    with database.transaction(write=True) as connection:
        collision = find_pc_name_collision(connection, name)
        if collision:
            raise ValidationError(f"A PC named '{collision['name']}' already exists.")
        rows = select_expense_rows(connection, ids)
        if len(rows) != len(ids):
            raise NotFoundError("One or more selected items no longer exist.")
        for row in rows:
            if row["pc_id"] is not None or row["sale_id"] is not None:
                raise ValidationError(f"'{row['name']}' is not available for assembly.")
        bounded_cents_total((row["price_cents"] for row in rows), "Combined PC cost")
        pc_id = inserted_id(
            connection.execute(
                "INSERT INTO pcs (name,status) VALUES (?,'active')", (name,)
            )
        )
        connection.executemany(
            "INSERT INTO pc_parts (pc_id,item_id,position) VALUES (?,?,?)",
            ((pc_id, expense_id, position) for position, expense_id in enumerate(ids)),
        )
        record_audit_event(
            connection, "assembled", "pc", pc_id, f"Assembled PC '{name}'."
        )
        return pc_id


def disassemble_pc(pc_id: int, *, database: Database) -> None:
    pc_id = positive_command_id(pc_id, "PC ID")
    with database.transaction(write=True) as connection:
        pc = connection.execute(
            "SELECT name,status FROM pcs WHERE id=?", (pc_id,)
        ).fetchone()
        if pc is None:
            raise NotFoundError(f"PC {pc_id} does not exist.")
        if pc["status"] != "active":
            raise ValidationError("Undo the PC sale before disassembling it.")
        result = connection.execute("DELETE FROM pcs WHERE id=?", (pc_id,))
        if result.rowcount != 1:
            raise NotFoundError(f"PC {pc_id} does not exist.")
        record_audit_event(
            connection,
            "disassembled",
            "pc",
            pc_id,
            f"Disassembled PC '{pc['name']}'.",
        )


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
    ids = unique_command_ids(expense_ids, "Item ID")
    with database.transaction(write=True) as connection:
        pc = connection.execute(
            "SELECT id,status FROM pcs WHERE id=?", (pc_id,)
        ).fetchone()
        if pc is None:
            raise NotFoundError(f"PC {pc_id} does not exist.")
        if pc["status"] != "active":
            raise ValidationError("Undo the PC sale before editing it.")
        collision = find_pc_name_collision(connection, name, exclude_id=pc_id)
        if collision:
            raise ValidationError(f"A PC named '{collision['name']}' already exists.")
        rows = select_expense_rows(connection, ids)
        if len(rows) != len(ids):
            raise NotFoundError("One or more selected items no longer exist.")
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
        connection.execute("DELETE FROM pc_parts WHERE pc_id=?", (pc_id,))
        connection.execute("UPDATE pcs SET name=? WHERE id=?", (name, pc_id))
        connection.executemany(
            "INSERT INTO pc_parts (pc_id,item_id,position) VALUES (?,?,?)",
            ((pc_id, expense_id, position) for position, expense_id in enumerate(ids)),
        )
        record_audit_event(connection, "updated", "pc", pc_id, f"Updated PC '{name}'.")
