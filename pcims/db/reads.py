"""Read-only projections over the current PCIMS schema."""

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import cast

from pcims.db.records import EXPENSE_SELECT, expense_from_row
from pcims.domain import ItemType, SaleKind
from pcims.models import AssembledPC, Expense, FinancialSummary, Sale


@dataclass(frozen=True, slots=True)
class ReadQueries:
    """Composable read operations over one caller-owned SQLite snapshot."""

    connection: sqlite3.Connection

    def list_expenses(self) -> tuple[Expense, ...]:
        rows = self.connection.execute(EXPENSE_SELECT + " ORDER BY e.id").fetchall()
        return tuple(expense_from_row(row) for row in rows)

    def count_expenses(self) -> int:
        return int(self.connection.execute("SELECT COUNT(*) FROM expenses").fetchone()[0])

    def list_expense_page(self, offset: int, limit: int) -> tuple[Expense, ...]:
        rows = self.connection.execute(
            EXPENSE_SELECT + " ORDER BY e.id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        return tuple(expense_from_row(row) for row in rows)

    def list_expense_names(self) -> tuple[str, ...]:
        rows = self.connection.execute(
            "SELECT DISTINCT name FROM expenses "
            "ORDER BY name COLLATE PCIMS_NOCASE,name"
        )
        return tuple(str(row[0]) for row in rows)

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
            + " ORDER BY e.item_type,e.name COLLATE PCIMS_NOCASE,e.id"
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

    def list_sales(
        self, expenses_by_id: Mapping[int, Expense] | None = None
    ) -> tuple[Sale, ...]:
        sales = self.connection.execute(
            "SELECT id,name,kind,selling_price_cents,sale_date "
            "FROM sales ORDER BY id"
        ).fetchall()
        items_by_sale: dict[int, list[Expense]] = {
            int(sale["id"]): [] for sale in sales
        }
        if expenses_by_id is None:
            rows = self.connection.execute(
                EXPENSE_SELECT
                + " WHERE si.sale_id IS NOT NULL ORDER BY si.sale_id,si.position"
            )
            for row in rows:
                items_by_sale[int(row["sale_id"])].append(expense_from_row(row))
        else:
            memberships = self.connection.execute(
                "SELECT sale_id,expense_id FROM sale_items "
                "ORDER BY sale_id,position"
            )
            for membership in memberships:
                items_by_sale[int(membership["sale_id"])].append(
                    expenses_by_id[int(membership["expense_id"])]
                )
        return tuple(
            Sale(
                id=sale["id"],
                name=sale["name"],
                kind=cast(SaleKind, sale["kind"]),
                cost_cents=sum(
                    item.price_cents for item in items_by_sale[sale["id"]]
                ),
                selling_price_cents=sale["selling_price_cents"],
                sale_date=date.fromisoformat(sale["sale_date"]),
                items=tuple(items_by_sale[sale["id"]]),
            )
            for sale in sales
        )

    def count_sales(self) -> int:
        return int(self.connection.execute("SELECT COUNT(*) FROM sales").fetchone()[0])

    def list_sale_page(
        self,
        offset: int,
        limit: int,
        expenses_by_id: Mapping[int, Expense] | None = None,
    ) -> tuple[Sale, ...]:
        sales = self.connection.execute(
            "SELECT id,name,kind,selling_price_cents,sale_date "
            "FROM sales ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        if not sales:
            return ()
        sale_ids = [int(sale["id"]) for sale in sales]
        placeholders = ",".join("?" for _ in sale_ids)
        rows = self.connection.execute(
            EXPENSE_SELECT
            + f" WHERE si.sale_id IN ({placeholders}) "  # nosec B608
            "ORDER BY si.sale_id,si.position",
            sale_ids,
        )
        items_by_sale: dict[int, list[Expense]] = {
            int(sale["id"]): [] for sale in sales
        }
        for row in rows:
            expense_id = int(row["id"])
            expense = (
                expenses_by_id.get(expense_id)
                if expenses_by_id is not None
                else None
            )
            items_by_sale[int(row["sale_id"])].append(
                expense if expense is not None else expense_from_row(row)
            )
        return tuple(
            Sale(
                id=sale["id"],
                name=sale["name"],
                kind=cast(SaleKind, sale["kind"]),
                cost_cents=sum(
                    item.price_cents for item in items_by_sale[sale["id"]]
                ),
                selling_price_cents=sale["selling_price_cents"],
                sale_date=date.fromisoformat(sale["sale_date"]),
                items=tuple(items_by_sale[sale["id"]]),
            )
            for sale in sales
        )

    def financial_summary(self) -> FinancialSummary:
        expense_cents, income_cents, cost_cents, inventory_cents = (
            self.connection.execute(
                """SELECT
                   (SELECT COALESCE(SUM(price_cents),0) FROM expenses),
                   (SELECT COALESCE(SUM(selling_price_cents),0) FROM sales),
                   (SELECT COALESCE(SUM(e.price_cents),0)
                      FROM sale_items si JOIN expenses e ON e.id=si.expense_id),
                   (SELECT COALESCE(SUM(e.price_cents),0) FROM expenses e
                      LEFT JOIN sale_items si ON si.expense_id=e.id
                     WHERE si.sale_id IS NULL)"""
            ).fetchone()
        )
        return FinancialSummary(
            expense_cents=expense_cents,
            income_cents=income_cents,
            profit_cents=income_cents - cost_cents,
            inventory_cents=inventory_cents,
        )
