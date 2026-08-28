"""Atomic laptop, extraction, replacement, and sale workflows."""

import sqlite3
from collections.abc import Iterable
from datetime import date
from typing import cast

from pcims.db.command_support import (
    bounded_cents_total,
    positive_command_id,
    select_expense_rows,
)
from pcims.db.connection import Database
from pcims.db.errors import NotFoundError, ValidationError
from pcims.db.expense_commands import _link_new_proofs, insert_expense_record
from pcims.db.lifecycle import require_row_transition
from pcims.db.records import inserted_id
from pcims.domain import (
    LaptopSlotRef,
    NewExpense,
    SaleTerms,
)
from pcims.lifecycle import InventoryState, LifecycleEvent
from pcims.proofs import NewProof, validate_proof_collection


def _laptop_row(connection: sqlite3.Connection, laptop_id: int) -> sqlite3.Row:
    row = connection.execute(
        """SELECT e.id,e.name,e.price_cents,e.purchase_date,si.sale_id,
                  c.cash_paid_cents
             FROM laptops l JOIN inventory_items e ON e.id=l.item_id
             JOIN item_costs c ON c.item_id=e.id
             LEFT JOIN sale_items si ON si.item_id=e.id
            WHERE l.item_id=?""",
        (laptop_id,),
    ).fetchone()
    if row is None:
        raise NotFoundError(f"Laptop {laptop_id} does not exist.")
    return cast(sqlite3.Row, row)


def add_laptop(
    laptop: NewExpense,
    proofs: Iterable[NewProof] = (),
    *,
    database: Database,
) -> int:
    if laptop.item_type != "Extra":
        raise ValidationError("A laptop must use the internal Extra item type.")
    proof_group = tuple(proofs)
    try:
        validate_proof_collection(proof_group)
    except (TypeError, ValueError) as error:
        raise ValidationError(str(error)) from error
    with database.transaction(write=True) as connection:
        laptop_id = insert_expense_record(connection, laptop)
        connection.execute("INSERT INTO laptops (item_id) VALUES (?)", (laptop_id,))
        _link_new_proofs(connection, laptop_id, proof_group)
        return laptop_id


def update_laptop(
    laptop_id: int,
    replacement: NewExpense,
    *,
    database: Database,
) -> None:
    laptop_id = positive_command_id(laptop_id, "Laptop ID")
    if replacement.item_type != "Extra":
        raise ValidationError("A laptop must use the internal Extra item type.")
    with database.transaction(write=True) as connection:
        laptop = _laptop_row(connection, laptop_id)
        earliest_sale = connection.execute(
            """SELECT MIN(s.sale_date) FROM sales s JOIN sale_items si ON si.sale_id=s.id
                WHERE si.item_id=? OR si.item_id IN (
                    SELECT extracted_item_id FROM laptop_slots WHERE laptop_id=?)""",
            (laptop_id, laptop_id),
        ).fetchone()[0]
        if (
            earliest_sale is not None
            and replacement.purchase_date.isoformat() > earliest_sale
        ):
            raise ValidationError(
                "Purchase date cannot be after a laptop or extracted-component sale."
            )
        extracted_total = int(
            connection.execute(
                """SELECT COALESCE(SUM(e.price_cents),0)
                     FROM laptop_slots ls JOIN inventory_items e
                       ON e.id=ls.extracted_item_id WHERE ls.laptop_id=?""",
                (laptop_id,),
            ).fetchone()[0]
        )
        if replacement.price_cents < extracted_total:
            raise ValidationError(
                "Purchase price cannot be lower than the value already transferred "
                "to extracted components."
            )
        installed_total = _installed_cost_cents(connection, laptop_id)
        bounded_cents_total(
            (replacement.price_cents - extracted_total, installed_total),
            "Combined laptop cost",
        )
        result = connection.execute(
            """UPDATE inventory_items SET name=?,price_cents=?,purchase_date=?,vendor=?,
                      serial_number=?,storage_location=?,condition=?,warranty_until=?,notes=?
                  WHERE id=?""",
            (
                replacement.name,
                replacement.price_cents - extracted_total,
                replacement.purchase_date.isoformat(),
                replacement.details.vendor,
                replacement.details.serial_number,
                replacement.details.storage_location,
                replacement.details.condition,
                replacement.details.warranty_until.isoformat()
                if replacement.details.warranty_until
                else None,
                replacement.details.notes,
                laptop_id,
            ),
        )
        if result.rowcount != 1:
            raise NotFoundError(f"Laptop {laptop_id} does not exist.")
        connection.execute(
            "UPDATE item_costs SET cash_paid_cents=? WHERE item_id=?",
            (replacement.price_cents, laptop_id),
        )
        connection.execute(
            """UPDATE inventory_items SET purchase_date=? WHERE id IN (
                   SELECT extracted_item_id FROM laptop_slots WHERE laptop_id=?)""",
            (replacement.purchase_date.isoformat(), laptop_id),
        )
        if laptop["sale_id"] is not None:
            connection.execute(
                "UPDATE sales SET name=? WHERE id=?",
                (replacement.name, laptop["sale_id"]),
            )


def _validate_replacement(
    connection: sqlite3.Connection, item_id: int, component_type: str
) -> None:
    row = connection.execute(
        """SELECT e.name,e.item_type,si.sale_id,pp.pc_id,ls.laptop_id,
                  CASE WHEN l.item_id IS NULL THEN 0 ELSE 1 END AS is_laptop
             FROM inventory_items e
             LEFT JOIN sale_items si ON si.item_id=e.id
             LEFT JOIN pc_parts pp ON pp.item_id=e.id
             LEFT JOIN laptop_slots ls ON ls.installed_item_id=e.id
             LEFT JOIN laptops l ON l.item_id=e.id WHERE e.id=?""",
        (item_id,),
    ).fetchone()
    if row is None:
        raise NotFoundError(f"Replacement item {item_id} does not exist.")
    if row["item_type"] != component_type:
        raise ValidationError(f"Replacement must be a {component_type} item.")
    require_row_transition(
        row, LifecycleEvent.INSTALL_IN_LAPTOP, InventoryState.LAPTOP_COMPONENT
    )


def _installed_cost_cents(
    connection: sqlite3.Connection,
    laptop_id: int,
    *,
    exclude_component_type: str | None = None,
    exclude_slot_number: int | None = None,
) -> int:
    if exclude_component_type is None or exclude_slot_number is None:
        row = connection.execute(
            """SELECT COALESCE(SUM(e.price_cents),0)
                 FROM laptop_slots ls JOIN inventory_items e
                   ON e.id=ls.installed_item_id WHERE ls.laptop_id=?""",
            (laptop_id,),
        ).fetchone()
    else:
        row = connection.execute(
            """SELECT COALESCE(SUM(e.price_cents),0)
                 FROM laptop_slots ls JOIN inventory_items e
                   ON e.id=ls.installed_item_id
                WHERE ls.laptop_id=?
                  AND NOT (ls.component_type=? AND ls.slot_number=?)""",
            (laptop_id, exclude_component_type, exclude_slot_number),
        ).fetchone()
    return int(row[0])


def extract_laptop_component(
    laptop_id: int,
    slot: LaptopSlotRef,
    extracted: NewExpense,
    installed_item_id: int | None = None,
    *,
    database: Database,
) -> int:
    laptop_id = positive_command_id(laptop_id, "Laptop ID")
    kind = slot.component_type
    slot_number = slot.slot_number
    if extracted.item_type != kind:
        raise ValidationError(f"Extracted component must be a {kind} item.")
    if extracted.price_cents <= 0:
        raise ValidationError(
            "A removed factory component must have a value above zero."
        )
    replacement_id = (
        positive_command_id(installed_item_id, "Replacement item ID")
        if installed_item_id is not None
        else None
    )
    with database.transaction(write=True) as connection:
        laptop = _laptop_row(connection, laptop_id)
        if laptop["sale_id"] is not None:
            raise ValidationError(
                "Undo the laptop sale before changing its components."
            )
        if int(laptop["price_cents"]) < extracted.price_cents:
            raise ValidationError(
                "The removed component value cannot exceed the laptop's remaining value."
            )
        exists = connection.execute(
            """SELECT 1 FROM laptop_slots
                WHERE laptop_id=? AND component_type=? AND slot_number=?""",
            (laptop_id, kind, slot_number),
        ).fetchone()
        if exists is not None:
            raise ValidationError(f"{kind} slot {slot_number} is already tracked.")
        if replacement_id is not None:
            _validate_replacement(connection, replacement_id, kind)
        installed_total = _installed_cost_cents(connection, laptop_id)
        replacement_cost = (
            int(
                connection.execute(
                    "SELECT price_cents FROM inventory_items WHERE id=?",
                    (replacement_id,),
                ).fetchone()[0]
            )
            if replacement_id is not None
            else 0
        )
        bounded_cents_total(
            (
                int(laptop["price_cents"]) - extracted.price_cents,
                installed_total,
                replacement_cost,
            ),
            "Combined laptop cost",
        )
        normalized_extracted = NewExpense(
            extracted.name,
            extracted.item_type,
            extracted.price_cents,
            date.fromisoformat(laptop["purchase_date"]),
            extracted.details,
        )
        extracted_id = insert_expense_record(
            connection,
            normalized_extracted,
            cash_paid_cents=0,
            origin="extracted",
        )
        connection.execute(
            "UPDATE inventory_items SET price_cents=price_cents-? WHERE id=?",
            (extracted.price_cents, laptop_id),
        )
        connection.execute(
            """INSERT INTO laptop_slots
               (laptop_id,component_type,slot_number,extracted_item_id,installed_item_id)
               VALUES (?,?,?,?,?)""",
            (laptop_id, kind, slot_number, extracted_id, replacement_id),
        )
        return extracted_id


def set_laptop_replacement(
    laptop_id: int,
    slot_ref: LaptopSlotRef,
    installed_item_id: int | None,
    *,
    database: Database,
) -> None:
    laptop_id = positive_command_id(laptop_id, "Laptop ID")
    kind = slot_ref.component_type
    slot_number = slot_ref.slot_number
    replacement_id = (
        positive_command_id(installed_item_id, "Replacement item ID")
        if installed_item_id is not None
        else None
    )
    with database.transaction(write=True) as connection:
        laptop = _laptop_row(connection, laptop_id)
        if laptop["sale_id"] is not None:
            raise ValidationError(
                "Undo the laptop sale before changing its components."
            )
        slot_row = connection.execute(
            """SELECT installed_item_id FROM laptop_slots
                WHERE laptop_id=? AND component_type=? AND slot_number=?""",
            (laptop_id, kind, slot_number),
        ).fetchone()
        if slot_row is None:
            raise NotFoundError(f"{kind} slot {slot_number} is not tracked.")
        previous_id = slot_row["installed_item_id"]
        if (
            replacement_id is not None
            and replacement_id != slot_row["installed_item_id"]
        ):
            _validate_replacement(connection, replacement_id, kind)
        if previous_id is not None and previous_id != replacement_id:
            previous = select_expense_rows(connection, [int(previous_id)])[0]
            require_row_transition(
                previous,
                LifecycleEvent.REMOVE_FROM_LAPTOP,
                InventoryState.AVAILABLE,
            )
        replacement_cost = (
            int(
                connection.execute(
                    "SELECT price_cents FROM inventory_items WHERE id=?",
                    (replacement_id,),
                ).fetchone()[0]
            )
            if replacement_id is not None
            else 0
        )
        bounded_cents_total(
            (
                int(laptop["price_cents"]),
                _installed_cost_cents(
                    connection,
                    laptop_id,
                    exclude_component_type=kind,
                    exclude_slot_number=slot_number,
                ),
                replacement_cost,
            ),
            "Combined laptop cost",
        )
        connection.execute(
            """UPDATE laptop_slots SET installed_item_id=?
                WHERE laptop_id=? AND component_type=? AND slot_number=?""",
            (replacement_id, laptop_id, kind, slot_number),
        )


def restore_laptop_component(
    laptop_id: int,
    slot: LaptopSlotRef,
    *,
    database: Database,
) -> None:
    laptop_id = positive_command_id(laptop_id, "Laptop ID")
    kind = slot.component_type
    slot_number = slot.slot_number
    with database.transaction(write=True) as connection:
        laptop = _laptop_row(connection, laptop_id)
        if laptop["sale_id"] is not None:
            raise ValidationError("Undo the laptop sale before restoring a component.")
        slot_row = connection.execute(
            """SELECT ls.extracted_item_id,ls.installed_item_id,e.price_cents,
                      si.sale_id,pp.pc_id,
                      installed.laptop_id AS installed_laptop_id
                 FROM laptop_slots ls JOIN inventory_items e
                   ON e.id=ls.extracted_item_id
                 LEFT JOIN sale_items si ON si.item_id=e.id
                 LEFT JOIN pc_parts pp ON pp.item_id=e.id
                 LEFT JOIN laptop_slots installed ON installed.installed_item_id=e.id
                WHERE ls.laptop_id=? AND ls.component_type=? AND ls.slot_number=?""",
            (laptop_id, kind, slot_number),
        ).fetchone()
        if slot_row is None:
            raise NotFoundError(f"{kind} slot {slot_number} is not tracked.")
        if slot_row["sale_id"] is not None:
            raise ValidationError(
                "Undo the extracted component sale before restoring it."
            )
        if slot_row["pc_id"] is not None:
            raise ValidationError(
                "Disassemble the PC using this factory component before restoring it."
            )
        if slot_row["installed_laptop_id"] is not None:
            raise ValidationError(
                "Remove this factory component from its current laptop before restoring it."
            )
        bounded_cents_total(
            (int(laptop["price_cents"]), int(slot_row["price_cents"])),
            "Restored laptop value",
        )
        if slot_row["installed_item_id"] is not None:
            installed = select_expense_rows(
                connection, [int(slot_row["installed_item_id"])]
            )[0]
            require_row_transition(
                installed,
                LifecycleEvent.REMOVE_FROM_LAPTOP,
                InventoryState.AVAILABLE,
            )
        connection.execute(
            """DELETE FROM laptop_slots
                WHERE laptop_id=? AND component_type=? AND slot_number=?""",
            (laptop_id, kind, slot_number),
        )


def delete_laptop(laptop_id: int, *, database: Database) -> None:
    laptop_id = positive_command_id(laptop_id, "Laptop ID")
    with database.transaction(write=True) as connection:
        laptop = _laptop_row(connection, laptop_id)
        if laptop["sale_id"] is not None:
            raise ValidationError("Undo the laptop sale before deleting it.")
        if (
            connection.execute(
                "SELECT 1 FROM laptop_slots WHERE laptop_id=? LIMIT 1", (laptop_id,)
            ).fetchone()
            is not None
        ):
            raise ValidationError(
                "Restore all tracked factory components before deleting the laptop."
            )
        connection.execute("DELETE FROM inventory_items WHERE id=?", (laptop_id,))


def sell_laptop(laptop_id: int, terms: SaleTerms, *, database: Database) -> int:
    laptop_id = positive_command_id(laptop_id, "Laptop ID")
    sale_day = terms.sale_date.isoformat()
    with database.transaction(write=True) as connection:
        laptop = _laptop_row(connection, laptop_id)
        if laptop["sale_id"] is not None:
            raise ValidationError(f"Laptop '{laptop['name']}' has already been sold.")
        rows = connection.execute(
            """SELECT e.id,e.price_cents,e.purchase_date,0 AS position
                 FROM inventory_items e WHERE e.id=?
               UNION ALL
               SELECT e.id,e.price_cents,e.purchase_date,
                      ls.slot_number AS position
                 FROM laptop_slots ls JOIN inventory_items e
                   ON e.id=ls.installed_item_id WHERE ls.laptop_id=?
                ORDER BY position,id""",
            (laptop_id, laptop_id),
        ).fetchall()
        lifecycle_rows = {
            int(row["id"]): row
            for row in select_expense_rows(connection, [int(row["id"]) for row in rows])
        }
        for row in rows:
            require_row_transition(
                lifecycle_rows[int(row["id"])],
                LifecycleEvent.SELL_LAPTOP,
                InventoryState.SOLD,
            )
        if any(sale_day < row["purchase_date"] for row in rows):
            raise ValidationError(
                "Sale date cannot be before an included purchase date."
            )
        bounded_cents_total((int(row["price_cents"]) for row in rows), "Laptop cost")
        sale_id = inserted_id(
            connection.execute(
                """INSERT INTO sales
                   (name,kind,pc_id,selling_price_cents,sale_date)
                   VALUES (?,'item',NULL,?,?)""",
                (laptop["name"], terms.selling_price_cents, sale_day),
            )
        )
        connection.execute(
            "INSERT INTO laptop_sales (sale_id,laptop_id) VALUES (?,?)",
            (sale_id, laptop_id),
        )
        connection.executemany(
            "INSERT INTO sale_items (sale_id,item_id,position) VALUES (?,?,?)",
            ((sale_id, row["id"], position) for position, row in enumerate(rows)),
        )
        return sale_id
