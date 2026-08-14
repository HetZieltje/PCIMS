"""Current-schema data access and atomic business workflows for PCIMS."""

import sqlite3
from collections.abc import Iterable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import date
from typing import cast

from pcims.db.connection import Database
from pcims.db.errors import NotFoundError, ValidationError
from pcims.db.models import AssembledPC, Expense, FinancialSummary, Sale
from pcims.domain import (
    ItemType,
    NewExpense,
    SaleKind,
    SaleTerms,
    normalized_id,
    normalized_ids,
    normalized_text,
)
from pcims.money import MAX_MONEY_CENTS


def _transaction(
    database: Database, *, write: bool = False
) -> AbstractContextManager[sqlite3.Connection]:
    return database.transaction(write=write)


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
    database: sqlite3.Connection, name: str, exclude_id: int | None = None
) -> sqlite3.Row | None:
    folded = name.casefold()
    return next(
        (
            row
            for row in database.execute("SELECT id,name FROM assembled_pcs")
            if row["id"] != exclude_id and row["name"].casefold() == folded
        ),
        None,
    )


def _expense_from_row(row: sqlite3.Row) -> Expense:
    return Expense(
        id=row["id"],
        name=row["name"],
        item_type=cast(ItemType, row["item_type"]),
        price_cents=row["price_cents"],
        purchase_date=date.fromisoformat(row["purchase_date"]),
        pc_id=row["pc_id"],
        pc_name=row["pc_name"],
        sale_id=row["sale_id"],
    )


_EXPENSE_SELECT = """
    SELECT e.id,e.name,e.item_type,e.price_cents,e.purchase_date,
           p.id AS pc_id,p.name AS pc_name,si.sale_id
      FROM expenses e
      LEFT JOIN pc_parts pp ON pp.expense_id=e.id
      LEFT JOIN assembled_pcs p ON p.id=pp.pc_id
      LEFT JOIN sale_items si ON si.expense_id=e.id
"""


def _insert_id(cursor: sqlite3.Cursor) -> int:
    row_id = cursor.lastrowid
    if row_id is None:
        raise sqlite3.DatabaseError("SQLite did not return an inserted row ID.")
    return row_id


@dataclass(frozen=True, slots=True)
class ReadQueries:
    """Composable read operations over one caller-owned SQLite snapshot."""

    connection: sqlite3.Connection

    def list_expenses(self) -> tuple[Expense, ...]:
        rows = self.connection.execute(_EXPENSE_SELECT + " ORDER BY e.id").fetchall()
        return tuple(_expense_from_row(row) for row in rows)

    def list_inventory(
        self, item_type: ItemType | None = None, available_only: bool = False
    ) -> tuple[Expense, ...]:
        clauses = ["si.sale_id IS NULL"]
        parameters: list[object] = []
        if item_type is not None:
            clauses.append("e.item_type=?")
            parameters.append(item_type)
        if available_only:
            clauses.append("p.id IS NULL")
        sql = (
            _EXPENSE_SELECT
            + " WHERE "
            + " AND ".join(clauses)
            + " ORDER BY e.item_type,e.name,e.id"
        )
        rows = self.connection.execute(sql, parameters).fetchall()
        return tuple(_expense_from_row(row) for row in rows)

    def list_pcs(self) -> tuple[AssembledPC, ...]:
        pcs = self.connection.execute(
            "SELECT id,name FROM assembled_pcs ORDER BY name,id"
        ).fetchall()
        rows = self.connection.execute(
            _EXPENSE_SELECT + " WHERE p.id IS NOT NULL ORDER BY p.id,pp.position"
        ).fetchall()
        parts_by_pc: dict[int, list[Expense]] = {int(pc["id"]): [] for pc in pcs}
        for row in rows:
            parts_by_pc[int(row["pc_id"])].append(_expense_from_row(row))
        return tuple(
            AssembledPC(pc["id"], pc["name"], tuple(parts_by_pc[pc["id"]]))
            for pc in pcs
        )

    def list_sales(self) -> tuple[Sale, ...]:
        sales = self.connection.execute(
            "SELECT id,name,kind,cost_cents,selling_price_cents,sale_date "
            "FROM sales ORDER BY id"
        ).fetchall()
        rows = self.connection.execute(
            _EXPENSE_SELECT
            + " WHERE si.sale_id IS NOT NULL ORDER BY si.sale_id,si.position"
        ).fetchall()
        items_by_sale: dict[int, list[Expense]] = {
            int(sale["id"]): [] for sale in sales
        }
        for row in rows:
            items_by_sale[int(row["sale_id"])].append(_expense_from_row(row))
        return tuple(
            Sale(
                id=sale["id"],
                name=sale["name"],
                kind=cast(SaleKind, sale["kind"]),
                cost_cents=sale["cost_cents"],
                selling_price_cents=sale["selling_price_cents"],
                sale_date=date.fromisoformat(sale["sale_date"]),
                items=tuple(items_by_sale[sale["id"]]),
            )
            for sale in sales
        )

    def financial_summary(self) -> FinancialSummary:
        expense_cents = self.connection.execute(
            "SELECT COALESCE(SUM(price_cents),0) FROM expenses"
        ).fetchone()[0]
        income_cents, cost_cents = self.connection.execute(
            "SELECT COALESCE(SUM(selling_price_cents),0),"
            "COALESCE(SUM(cost_cents),0) FROM sales"
        ).fetchone()
        inventory_cents = self.connection.execute(
            """SELECT COALESCE(SUM(e.price_cents),0) FROM expenses e
               LEFT JOIN sale_items si ON si.expense_id=e.id
               WHERE si.sale_id IS NULL"""
        ).fetchone()[0]
        return FinancialSummary(
            expense_cents=expense_cents,
            income_cents=income_cents,
            profit_cents=income_cents - cost_cents,
            inventory_cents=inventory_cents,
        )


def add_expenses(
    items: Iterable[NewExpense], *, database: Database
) -> list[int]:
    """Record one or more purchased items atomically."""
    expenses = tuple(items)
    if not expenses:
        raise ValidationError("At least one purchase item is required.")
    with _transaction(database, write=True) as connection:
        return [
            _insert_id(
                connection.execute(
                    "INSERT INTO expenses (name,item_type,price_cents,purchase_date) VALUES (?,?,?,?)",
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


def list_expenses(*, database: Database) -> tuple[Expense, ...]:
    with _transaction(database) as connection:
        return ReadQueries(connection).list_expenses()


def list_inventory(
    item_type: ItemType | None = None,
    available_only: bool = False,
    *,
    database: Database,
) -> tuple[Expense, ...]:
    with _transaction(database) as connection:
        return ReadQueries(connection).list_inventory(item_type, available_only)


def delete_expenses(
    expense_ids: Iterable[int], *, database: Database
) -> None:
    ids = _unique_ids(expense_ids, "Expense ID")
    placeholders = ",".join("?" for _ in ids)
    with _transaction(database, write=True) as connection:
        rows = connection.execute(
            _EXPENSE_SELECT + f" WHERE e.id IN ({placeholders})", ids
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
    expense_ids: Iterable[int],
    new_name: str,
    *,
    database: Database,
) -> None:
    ids = _unique_ids(expense_ids, "Expense ID")
    new_name = _text(new_name, "New item name")
    placeholders = ",".join("?" for _ in ids)
    with _transaction(database, write=True) as connection:
        count = connection.execute(
            # IDs are validated integers; only the number of bound placeholders varies.
            f"SELECT COUNT(*) FROM expenses WHERE id IN ({placeholders})",  # nosec B608
            ids,
        ).fetchone()[0]
        if count != len(ids):
            raise NotFoundError("One or more selected expenses no longer exist.")
        connection.execute(
            # IDs and name remain bound parameters; no user text enters the SQL string.
            f"UPDATE expenses SET name=? WHERE id IN ({placeholders})",  # nosec B608
            [new_name, *ids],
        )


def assemble_pc(
    name: str,
    expense_ids: Iterable[int],
    *,
    database: Database,
) -> int:
    name = _text(name, "PC name")
    ids = _unique_ids(expense_ids, "Expense ID")
    placeholders = ",".join("?" for _ in ids)
    with _transaction(database, write=True) as connection:
        collision = _find_pc_name_collision(connection, name)
        if collision:
            raise ValidationError(f"A PC named '{collision['name']}' already exists.")
        rows = connection.execute(
            _EXPENSE_SELECT + f" WHERE e.id IN ({placeholders})", ids
        ).fetchall()
        if len(rows) != len(ids):
            raise NotFoundError("One or more selected expenses no longer exist.")
        for row in rows:
            if row["pc_id"] is not None or row["sale_id"] is not None:
                raise ValidationError(f"'{row['name']}' is not available for assembly.")
        pc_id = _insert_id(
            connection.execute("INSERT INTO assembled_pcs (name) VALUES (?)", (name,))
        )
        connection.executemany(
            "INSERT INTO pc_parts (pc_id,expense_id,position) VALUES (?,?,?)",
            ((pc_id, expense_id, position) for position, expense_id in enumerate(ids)),
        )
        return pc_id


def list_pcs(*, database: Database) -> tuple[AssembledPC, ...]:
    with _transaction(database) as connection:
        return ReadQueries(connection).list_pcs()


def disassemble_pc(pc_id: int, *, database: Database) -> None:
    pc_id = _positive_id(pc_id, "PC ID")
    with _transaction(database, write=True) as connection:
        result = connection.execute("DELETE FROM assembled_pcs WHERE id=?", (pc_id,))
        if result.rowcount != 1:
            raise NotFoundError(f"PC {pc_id} does not exist.")


def rename_pc(
    pc_id: int, new_name: str, *, database: Database
) -> None:
    pc_id = _positive_id(pc_id, "PC ID")
    new_name = _text(new_name, "New PC name")
    with _transaction(database, write=True) as connection:
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
    expense_ids: Iterable[int],
    terms: SaleTerms,
    *,
    database: Database,
) -> int:
    ids = _unique_ids(expense_ids, "Expense ID")
    sale_day = terms.sale_date.isoformat()
    placeholders = ",".join("?" for _ in ids)
    with _transaction(database, write=True) as connection:
        rows = connection.execute(
            _EXPENSE_SELECT + f" WHERE e.id IN ({placeholders}) ORDER BY e.id", ids
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
        sale_id = _insert_id(
            connection.execute(
                "INSERT INTO sales (name,kind,cost_cents,selling_price_cents,sale_date) VALUES (?,'item',?,?,?)",
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


def sell_pc(
    pc_id: int,
    terms: SaleTerms,
    *,
    database: Database,
) -> int:
    pc_id = _positive_id(pc_id, "PC ID")
    sale_day = terms.sale_date.isoformat()
    with _transaction(database, write=True) as connection:
        pc = connection.execute(
            "SELECT id,name FROM assembled_pcs WHERE id=?", (pc_id,)
        ).fetchone()
        if pc is None:
            raise NotFoundError(f"PC {pc_id} does not exist.")
        rows = connection.execute(
            _EXPENSE_SELECT + " WHERE p.id=? ORDER BY pp.position", (pc_id,)
        ).fetchall()
        if not rows:
            raise ValidationError(f"PC '{pc['name']}' has no components.")
        _validate_sale_date(rows, sale_day)
        cost_cents = _bounded_cents_total(
            (row["price_cents"] for row in rows), "Combined PC cost"
        )
        expense_ids = [row["id"] for row in rows]
        connection.execute("DELETE FROM assembled_pcs WHERE id=?", (pc_id,))
        sale_id = _insert_id(
            connection.execute(
                "INSERT INTO sales (name,kind,cost_cents,selling_price_cents,sale_date) VALUES (?,'pc',?,?,?)",
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


def list_sales(*, database: Database) -> tuple[Sale, ...]:
    with _transaction(database) as connection:
        return ReadQueries(connection).list_sales()


def undo_sale(sale_id: int, *, database: Database) -> None:
    sale_id = _positive_id(sale_id, "Sale ID")
    with _transaction(database, write=True) as connection:
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
                    f"Cannot undo while an assembled PC named '{collision['name']}' exists."
                )
            connection.execute("DELETE FROM sales WHERE id=?", (sale_id,))
            pc_id = _insert_id(
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


def get_financial_summary(*, database: Database) -> FinancialSummary:
    with _transaction(database) as connection:
        return ReadQueries(connection).financial_summary()
