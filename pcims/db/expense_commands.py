"""Atomic purchase and expense write workflows."""

import hashlib
import sqlite3
from collections.abc import Iterable

from pcims.db.audit import record_audit_event
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
from pcims.proofs import MAX_PROOFS_PER_ITEM, NewProof, validate_proof_collection


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
        "SELECT id FROM proof_files WHERE sha256=? AND file_name=?",
        (digest, proof.file_name),
    ).fetchone()
    if existing is not None:
        proof_id = int(existing["id"])
    else:
        proof_id = inserted_id(
            connection.execute(
                """INSERT INTO proof_files (file_name,media_type,content,sha256)
                   VALUES (?,?,?,?)""",
                (proof.file_name, proof.media_type, proof.content, digest),
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
        "INSERT INTO expense_proofs (expense_id,proof_id,position) VALUES (?,?,?)",
        (
            (expense_id, proof_id, start_position + position)
            for position, proof_id in enumerate(proof_ids)
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
            _link_new_proofs(
                connection,
                expense_id,
                proofs,
                proof_id_cache=proof_id_cache,
            )
            connection.execute(
                """UPDATE expense_details
                      SET vendor=?,serial_number=?,storage_location=?,condition=?,
                          warranty_until=?,notes=?
                    WHERE expense_id=?""",
                (
                    expense.details.vendor,
                    expense.details.serial_number,
                    expense.details.storage_location,
                    expense.details.condition,
                    expense.details.warranty_until.isoformat()
                    if expense.details.warranty_until
                    else None,
                    expense.details.notes,
                    expense_id,
                ),
            )
            record_audit_event(
                connection,
                "created",
                "expense",
                expense_id,
                f"Added {expense.item_type} '{expense.name}'.",
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
    expense_id = positive_command_id(expense_id, "Expense ID")
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
            "SELECT 1 FROM expenses WHERE id=?", (expense_id,)
        ).fetchone()
        if exists is None:
            raise NotFoundError(f"Expense {expense_id} does not exist.")
        current_rows = connection.execute(
            """SELECT ep.proof_id,ep.position,pf.file_name
                 FROM expense_proofs ep
                 JOIN proof_files pf ON pf.id=ep.proof_id
                WHERE ep.expense_id=? ORDER BY ep.position""",
            (expense_id,),
        ).fetchall()
        current = {int(row["proof_id"]): row for row in current_rows}
        missing = next(
            (proof_id for proof_id in retained if proof_id not in current), None
        )
        if missing is not None:
            raise ValidationError(
                f"Proof {missing} is not attached to expense {expense_id}."
            )
        names = [
            str(current[proof_id]["file_name"]).casefold() for proof_id in retained
        ]
        names.extend(proof.file_name.casefold() for proof in additions)
        if len(names) != len(set(names)):
            raise ValidationError("Proof file names must be unique for each item.")

        removed = tuple(proof_id for proof_id in current if proof_id not in retained)
        connection.executemany(
            "DELETE FROM expense_proofs WHERE expense_id=? AND proof_id=?",
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
        record_audit_event(
            connection,
            "proofs_updated",
            "expense",
            expense_id,
            f"Updated proofs: {len(retained)} kept, {len(additions)} added.",
        )


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
        for row in rows:
            record_audit_event(
                connection,
                "deleted",
                "expense",
                int(row["id"]),
                f"Deleted {row['item_type']} '{row['name']}'.",
            )
        connection.executemany("DELETE FROM expenses WHERE id=?", ((i,) for i in ids))


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

        connection.execute(
            """UPDATE expense_details
                  SET vendor=?,serial_number=?,storage_location=?,condition=?,
                      warranty_until=?,notes=?
                WHERE expense_id=?""",
            (
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
        record_audit_event(
            connection,
            "updated",
            "expense",
            expense_id,
            f"Updated {replacement.item_type} '{replacement.name}'.",
        )
