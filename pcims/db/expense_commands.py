"""Atomic inventory-item and proof write workflows."""

import hashlib
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
from pcims.db.records import inserted_id
from pcims.domain import NewExpense
from pcims.proofs import (
    MAX_PROOFS_PER_ITEM,
    MAX_TOTAL_PROOF_BYTES,
    NewProof,
    validate_proof_collection,
)


def _proof_id(
    connection: sqlite3.Connection,
    proof: NewProof,
    cache: dict[int, int],
) -> int:
    cached = cache.get(id(proof))
    if cached is not None:
        return cached
    digest = hashlib.sha256(proof.content).hexdigest()
    existing = connection.execute(
        "SELECT id FROM proof_files WHERE sha256=?", (digest,)
    ).fetchone()
    if existing is not None:
        proof_id = int(existing["id"])
    else:
        stored_bytes = int(
            connection.execute(
                "SELECT COALESCE(SUM(length(content)),0) FROM proof_files"
            ).fetchone()[0]
        )
        if stored_bytes + len(proof.content) > MAX_TOTAL_PROOF_BYTES:
            raise ValidationError(
                "Stored proofs cannot exceed 512 MiB in total. Remove unused proofs "
                "or keep the receipt outside PCIMS."
            )
        proof_id = inserted_id(
            connection.execute(
                """INSERT INTO proof_files (media_type,content,sha256)
                   VALUES (?,?,?)""",
                (proof.media_type, proof.content, digest),
            )
        )
    cache[id(proof)] = proof_id
    return proof_id


def _link_new_proofs(
    connection: sqlite3.Connection,
    expense_id: int,
    proofs: tuple[NewProof, ...],
    start_position: int = 0,
    proof_id_cache: dict[int, int] | None = None,
) -> None:
    cache = {} if proof_id_cache is None else proof_id_cache
    proof_ids = tuple(_proof_id(connection, proof, cache) for proof in proofs)
    if len(proof_ids) != len(set(proof_ids)):
        raise ValidationError("The same proof file cannot be attached twice.")
    connection.executemany(
        "INSERT INTO item_proofs (item_id,proof_id,file_name,position) VALUES (?,?,?,?)",
        (
            (expense_id, proof_id, proof.file_name, start_position + position)
            for position, (proof_id, proof) in enumerate(
                zip(proof_ids, proofs, strict=True)
            )
        ),
    )


def add_expenses(
    items: Iterable[NewExpense],
    proofs_by_item: Iterable[Iterable[NewProof]] | None = None,
    *,
    database: Database,
) -> list[int]:
    """Record one or more purchased items atomically."""
    expenses = tuple(items)
    if not expenses:
        raise ValidationError("At least one purchase item is required.")
    proof_groups = (
        tuple(() for _expense in expenses)
        if proofs_by_item is None
        else tuple(tuple(proofs) for proofs in proofs_by_item)
    )
    if len(proof_groups) != len(expenses):
        raise ValidationError("Every purchase item must have one proof collection.")
    try:
        for proofs in proof_groups:
            validate_proof_collection(proofs)
    except (TypeError, ValueError) as error:
        raise ValidationError(str(error)) from error
    with database.transaction(write=True) as connection:
        identifiers: list[int] = []
        proof_id_cache: dict[int, int] = {}
        for expense, proofs in zip(expenses, proof_groups, strict=True):
            expense_id = inserted_id(
                connection.execute(
                    "INSERT INTO inventory_items "
                    "(name,item_type,price_cents,purchase_date,vendor,serial_number,"
                    "storage_location,condition,warranty_until,notes) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        expense.name,
                        expense.item_type,
                        expense.price_cents,
                        expense.purchase_date.isoformat(),
                        expense.details.vendor,
                        expense.details.serial_number,
                        expense.details.storage_location,
                        expense.details.condition,
                        expense.details.warranty_until.isoformat()
                        if expense.details.warranty_until
                        else None,
                        expense.details.notes,
                    ),
                )
            )
            _link_new_proofs(
                connection,
                expense_id,
                proofs,
                proof_id_cache=proof_id_cache,
            )
            identifiers.append(expense_id)
        return identifiers


def replace_expense_proofs(
    expense_id: int,
    retained_proof_ids: Iterable[int],
    new_proofs: Iterable[NewProof],
    *,
    database: Database,
) -> None:
    """Replace one item's proof collection without changing purchase history."""
    expense_id = positive_command_id(expense_id, "Item ID")
    retained = tuple(
        positive_command_id(proof_id, "Proof ID") for proof_id in retained_proof_ids
    )
    if len(retained) != len(set(retained)):
        raise ValidationError("Duplicate proof IDs are not allowed.")
    additions = tuple(new_proofs)
    try:
        validate_proof_collection(additions)
    except (TypeError, ValueError) as error:
        raise ValidationError(str(error)) from error
    if len(retained) + len(additions) > MAX_PROOFS_PER_ITEM:
        raise ValidationError(f"An item can have at most {MAX_PROOFS_PER_ITEM} proofs.")

    with database.transaction(write=True) as connection:
        exists = connection.execute(
            "SELECT 1 FROM inventory_items WHERE id=?", (expense_id,)
        ).fetchone()
        if exists is None:
            raise NotFoundError(f"Item {expense_id} does not exist.")
        current_rows = connection.execute(
            """SELECT ip.proof_id,ip.position,ip.file_name
                 FROM item_proofs ip
                WHERE ip.item_id=? ORDER BY ip.position""",
            (expense_id,),
        ).fetchall()
        current = {int(row["proof_id"]): row for row in current_rows}
        missing = next(
            (proof_id for proof_id in retained if proof_id not in current), None
        )
        if missing is not None:
            raise ValidationError(
                f"Proof {missing} is not attached to item {expense_id}."
            )
        names = [
            str(current[proof_id]["file_name"]).casefold() for proof_id in retained
        ]
        names.extend(proof.file_name.casefold() for proof in additions)
        if len(names) != len(set(names)):
            raise ValidationError("Proof file names must be unique for each item.")

        removed = tuple(proof_id for proof_id in current if proof_id not in retained)
        connection.executemany(
            "DELETE FROM item_proofs WHERE item_id=? AND proof_id=?",
            ((expense_id, proof_id) for proof_id in removed),
        )
        next_position = (
            max(
                (int(current[proof_id]["position"]) for proof_id in retained),
                default=-1,
            )
            + 1
        )
        _link_new_proofs(connection, expense_id, additions, next_position)


def delete_expenses(expense_ids: Iterable[int], *, database: Database) -> None:
    ids = unique_command_ids(expense_ids, "Item ID")
    with database.transaction(write=True) as connection:
        rows = select_expense_rows(connection, ids)
        if len(rows) != len(ids):
            found = {row["id"] for row in rows}
            missing = next(item_id for item_id in ids if item_id not in found)
            raise NotFoundError(f"Item {missing} does not exist.")
        for row in rows:
            if row["pc_id"] is not None:
                raise ValidationError(
                    f"Item {row['id']} belongs to PC '{row['pc_name']}'."
                )
            if row["sale_id"] is not None:
                raise ValidationError(
                    f"Item {row['id']} has sale history. Undo the sale first."
                )
        connection.executemany(
            "DELETE FROM inventory_items WHERE id=?", ((i,) for i in ids)
        )


def update_expense(
    expense_id: int,
    replacement: NewExpense,
    *,
    database: Database,
) -> None:
    """Replace every editable field while preserving the item identity."""
    expense_id = positive_command_id(expense_id, "Item ID")
    with database.transaction(write=True) as connection:
        rows = select_expense_rows(connection, [expense_id])
        if not rows:
            raise NotFoundError(f"Item {expense_id} does not exist.")
        row = rows[0]
        pc_id = int(row["pc_id"]) if row["pc_id"] is not None else None
        if pc_id is not None:
            membership = connection.execute(
                """SELECT pp.item_id,e.price_cents
                     FROM pc_parts pp JOIN inventory_items e ON e.id=pp.item_id
                    WHERE pp.pc_id=? ORDER BY pp.position""",
                (pc_id,),
            ).fetchall()
            bounded_cents_total(
                (
                    replacement.price_cents
                    if int(part["item_id"]) == expense_id
                    else int(part["price_cents"])
                    for part in membership
                ),
                "Combined PC cost",
            )

        linked_sale = connection.execute(
            """SELECT s.id,s.sale_date FROM sales s
               JOIN sale_items si ON si.sale_id=s.id
               WHERE si.item_id=?""",
            (expense_id,),
        ).fetchone()
        if (
            linked_sale is not None
            and replacement.purchase_date.isoformat() > linked_sale["sale_date"]
        ):
            raise ValidationError(
                "Purchase date cannot be after the recorded sale date."
            )
        if linked_sale is not None:
            sale_items = connection.execute(
                """SELECT si.item_id,i.price_cents FROM sale_items si
                   JOIN inventory_items i ON i.id=si.item_id
                   WHERE si.sale_id=?""",
                (linked_sale["id"],),
            )
            bounded_cents_total(
                (
                    replacement.price_cents
                    if int(item["item_id"]) == expense_id
                    else int(item["price_cents"])
                    for item in sale_items
                ),
                "Combined sale cost",
            )

        result = connection.execute(
            """UPDATE inventory_items
                  SET name=?,item_type=?,price_cents=?,purchase_date=?,vendor=?,
                      serial_number=?,storage_location=?,condition=?,warranty_until=?,notes=?
                WHERE id=?""",
            (
                replacement.name,
                replacement.item_type,
                replacement.price_cents,
                replacement.purchase_date.isoformat(),
                replacement.details.vendor,
                replacement.details.serial_number,
                replacement.details.storage_location,
                replacement.details.condition,
                replacement.details.warranty_until.isoformat()
                if replacement.details.warranty_until
                else None,
                replacement.details.notes,
                expense_id,
            ),
        )
        if result.rowcount != 1:
            raise NotFoundError(f"Item {expense_id} does not exist.")
