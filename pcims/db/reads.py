"""Read-only projections over the current PCIMS schema."""

import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import cast

from pcims.db.connection import Database
from pcims.db.models import AssembledPC, Expense, FinancialSummary, Sale
from pcims.db.records import EXPENSE_SELECT, expense_from_row
from pcims.domain import ItemType, SaleKind


@dataclass(frozen=True, slots=True)
class ReadQueries:
    """Composable read operations over one caller-owned SQLite snapshot."""

    connection: sqlite3.Connection

    def list_expenses(self) -> tuple[Expense, ...]:
        rows = self.connection.execute(EXPENSE_SELECT + " ORDER BY e.id").fetchall()
        return tuple(expense_from_row(row) for row in rows)

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
            EXPENSE_SELECT
            + " WHERE "
            + " AND ".join(clauses)
            + " ORDER BY e.item_type,e.name,e.id"
        )
        rows = self.connection.execute(sql, parameters).fetchall()
        return tuple(expense_from_row(row) for row in rows)

    def list_pcs(self) -> tuple[AssembledPC, ...]:
        pcs = self.connection.execute(
            "SELECT id,name FROM assembled_pcs ORDER BY name,id"
        ).fetchall()
        rows = self.connection.execute(
            EXPENSE_SELECT + " WHERE p.id IS NOT NULL ORDER BY p.id,pp.position"
        ).fetchall()
        parts_by_pc: dict[int, list[Expense]] = {int(pc["id"]): [] for pc in pcs}
        for row in rows:
            parts_by_pc[int(row["pc_id"])].append(expense_from_row(row))
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
            EXPENSE_SELECT
            + " WHERE si.sale_id IS NOT NULL ORDER BY si.sale_id,si.position"
        ).fetchall()
        items_by_sale: dict[int, list[Expense]] = {
            int(sale["id"]): [] for sale in sales
        }
        for row in rows:
            items_by_sale[int(row["sale_id"])].append(expense_from_row(row))
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


def list_expenses(*, database: Database) -> tuple[Expense, ...]:
    with database.transaction() as connection:
        return ReadQueries(connection).list_expenses()


def list_inventory(
    item_type: ItemType | None = None,
    available_only: bool = False,
    *,
    database: Database,
) -> tuple[Expense, ...]:
    with database.transaction() as connection:
        return ReadQueries(connection).list_inventory(item_type, available_only)


def list_pcs(*, database: Database) -> tuple[AssembledPC, ...]:
    with database.transaction() as connection:
        return ReadQueries(connection).list_pcs()


def list_sales(*, database: Database) -> tuple[Sale, ...]:
    with database.transaction() as connection:
        return ReadQueries(connection).list_sales()


def get_financial_summary(*, database: Database) -> FinancialSummary:
    with database.transaction() as connection:
        return ReadQueries(connection).financial_summary()
