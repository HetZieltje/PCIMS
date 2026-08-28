"""Atomic assembled-PC write workflows."""

from collections.abc import Iterable

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
from pcims.db.lifecycle import placement_from_row, require_row_transition
from pcims.db.records import inserted_id
from pcims.lifecycle import InventoryState, LifecycleEvent


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
            require_row_transition(
                row, LifecycleEvent.ASSEMBLE, InventoryState.PC_COMPONENT
            )
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
        rows = connection.execute(
            """SELECT e.name,pp.pc_id,NULL AS laptop_id,0 AS is_laptop,
                      NULL AS sale_id
                 FROM pc_parts pp JOIN inventory_items e ON e.id=pp.item_id
                WHERE pp.pc_id=?""",
            (pc_id,),
        ).fetchall()
        for row in rows:
            require_row_transition(
                row, LifecycleEvent.DISASSEMBLE, InventoryState.AVAILABLE
            )
        result = connection.execute("DELETE FROM pcs WHERE id=?", (pc_id,))
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
        existing_ids = {
            int(row[0])
            for row in connection.execute(
                "SELECT item_id FROM pc_parts WHERE pc_id=?", (pc_id,)
            )
        }
        for expense_id in ids:
            row = rows_by_id[expense_id]
            if expense_id in existing_ids:
                if placement_from_row(row).state is not InventoryState.PC_COMPONENT:
                    raise ValidationError(f"'{row['name']}' is not an active PC part.")
            else:
                require_row_transition(
                    row, LifecycleEvent.ASSEMBLE, InventoryState.PC_COMPONENT
                )
        for removed_id in existing_ids.difference(ids):
            removed_row = rows_by_id.get(removed_id)
            if removed_row is None:
                removed_row = connection.execute(
                    """SELECT e.name,pp.pc_id,NULL AS laptop_id,0 AS is_laptop,
                              NULL AS sale_id
                         FROM pc_parts pp JOIN inventory_items e ON e.id=pp.item_id
                        WHERE pp.pc_id=? AND pp.item_id=?""",
                    (pc_id, removed_id),
                ).fetchone()
            if removed_row is not None:
                require_row_transition(
                    removed_row,
                    LifecycleEvent.DISASSEMBLE,
                    InventoryState.AVAILABLE,
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
