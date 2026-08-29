"""Read-only projections over the current PCIMS schema."""

import hashlib
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import cast

from pcims.db.command_support import id_batches
from pcims.db.errors import DatabaseIntegrityError, NotFoundError
from pcims.db.records import EXPENSE_SELECT, expense_from_row
from pcims.domain import ItemType, LaptopComponentType, SaleKind
from pcims.models import (
    AssembledPC,
    BalanceBucket,
    BalancePoint,
    BalanceSummary,
    Expense,
    FinancialSummary,
    Laptop,
    LaptopSlot,
    Sale,
    SaleSummary,
)
from pcims.proofs import NewProof, ProofSummary


@dataclass(frozen=True, slots=True)
class ReadQueries:
    """Composable read operations over one caller-owned SQLite snapshot."""

    connection: sqlite3.Connection

    def _proofs_by_expense(
        self, expense_ids: tuple[int, ...]
    ) -> dict[int, tuple[ProofSummary, ...]]:
        grouped: dict[int, list[ProofSummary]] = {
            expense_id: [] for expense_id in expense_ids
        }
        for chunk in id_batches(list(expense_ids)):
            placeholders = ",".join("?" for _ in chunk)
            rows = self.connection.execute(
                # Only the internally generated placeholder count changes SQL.
                f"""SELECT ip.item_id AS expense_id,pf.id,ip.file_name,pf.media_type,
                           length(pf.content) AS size_bytes
                      FROM item_proofs ip
                      JOIN proof_files pf ON pf.id=ip.proof_id
                     WHERE ip.item_id IN ({placeholders})
                     ORDER BY ip.item_id,ip.position""",  # nosec B608
                chunk,
            )
            for row in rows:
                grouped[int(row["expense_id"])].append(
                    ProofSummary(
                        id=row["id"],
                        file_name=row["file_name"],
                        media_type=row["media_type"],
                        size_bytes=row["size_bytes"],
                    )
                )
        return {expense_id: tuple(proofs) for expense_id, proofs in grouped.items()}

    def _expenses_from_rows(self, rows: Iterable[sqlite3.Row]) -> tuple[Expense, ...]:
        materialized: tuple[sqlite3.Row, ...] = tuple(rows)
        proofs = self._proofs_by_expense(tuple(int(row["id"]) for row in materialized))
        return tuple(
            expense_from_row(row, proofs.get(int(row["id"]), ()))
            for row in materialized
        )

    def list_expenses(self) -> tuple[Expense, ...]:
        rows = self.connection.execute(
            EXPENSE_SELECT + " WHERE c.origin='purchase' ORDER BY e.id"
        ).fetchall()
        return self._expenses_from_rows(rows)

    def count_expenses(self, search: str = "") -> int:
        if search:
            return int(
                self.connection.execute(
                    """SELECT COUNT(*) FROM inventory_items e
                       JOIN item_costs c ON c.item_id=e.id
                       WHERE c.origin='purchase' AND
                            (PCIMS_CONTAINS(e.name,?)
                          OR PCIMS_CONTAINS(e.item_type,?)
                          OR PCIMS_CONTAINS(e.vendor,?)
                          OR PCIMS_CONTAINS(e.serial_number,?)
                          OR PCIMS_CONTAINS(e.storage_location,?)
                          OR PCIMS_CONTAINS(e.notes,?))""",
                    (search,) * 6,
                ).fetchone()[0]
            )
        return int(
            self.connection.execute(
                "SELECT COUNT(*) FROM item_costs WHERE origin='purchase'"
            ).fetchone()[0]
        )

    def list_expense_page(
        self, offset: int, limit: int, search: str = ""
    ) -> tuple[Expense, ...]:
        where = " WHERE c.origin='purchase'"
        parameters: tuple[object, ...] = ()
        if search:
            where = (
                " WHERE c.origin='purchase' AND (PCIMS_CONTAINS(e.name,?)"
                " OR PCIMS_CONTAINS(e.item_type,?) OR PCIMS_CONTAINS(e.vendor,?)"
                " OR PCIMS_CONTAINS(e.serial_number,?)"
                " OR PCIMS_CONTAINS(e.storage_location,?)"
                " OR PCIMS_CONTAINS(e.notes,?))"
            )
            parameters = (search,) * 6
        rows = self.connection.execute(
            EXPENSE_SELECT + where + " ORDER BY e.id DESC LIMIT ? OFFSET ?",
            (*parameters, limit, offset),
        )
        return self._expenses_from_rows(rows)

    def list_expense_names(self) -> tuple[str, ...]:
        rows = self.connection.execute(
            """SELECT DISTINCT e.name FROM inventory_items e
               JOIN item_costs c ON c.item_id=e.id WHERE c.origin='purchase'
               ORDER BY e.name COLLATE PCIMS_NOCASE,e.name"""
        )
        return tuple(str(row[0]) for row in rows)

    def list_inventory(
        self, item_type: ItemType | None = None, available_only: bool = False
    ) -> tuple[Expense, ...]:
        clauses = ["si.sale_id IS NULL", "l.item_id IS NULL", "ls.laptop_id IS NULL"]
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
        return self._expenses_from_rows(rows)

    def list_pcs(self) -> tuple[AssembledPC, ...]:
        pcs = self.connection.execute(
            "SELECT id,name FROM pcs WHERE status='active' ORDER BY name,id"
        ).fetchall()
        rows = self.connection.execute(
            EXPENSE_SELECT + " WHERE p.status='active' ORDER BY p.id,pp.position"
        ).fetchall()
        parts_by_pc: dict[int, list[Expense]] = {int(pc["id"]): [] for pc in pcs}
        for expense in self._expenses_from_rows(rows):
            if expense.pc_id is not None:
                parts_by_pc[expense.pc_id].append(expense)
        return tuple(
            AssembledPC(pc["id"], pc["name"], tuple(parts_by_pc[pc["id"]]))
            for pc in pcs
        )

    def list_laptops(self) -> tuple[Laptop, ...]:
        laptop_rows = self.connection.execute(
            """SELECT l.item_id,c.cash_paid_cents
                 FROM laptops l JOIN item_costs c ON c.item_id=l.item_id
                ORDER BY l.item_id"""
        ).fetchall()
        if not laptop_rows:
            return ()
        slot_rows = self.connection.execute(
            """SELECT laptop_id,component_type,slot_number,
                      extracted_item_id,installed_item_id
                 FROM laptop_slots
                ORDER BY laptop_id,component_type,slot_number"""
        ).fetchall()
        item_ids = {int(row["item_id"]) for row in laptop_rows}
        for row in slot_rows:
            item_ids.add(int(row["extracted_item_id"]))
            if row["installed_item_id"] is not None:
                item_ids.add(int(row["installed_item_id"]))
        item_rows: list[sqlite3.Row] = []
        for batch in id_batches(sorted(item_ids)):
            placeholders = ",".join("?" for _ in batch)
            item_rows.extend(
                self.connection.execute(
                    EXPENSE_SELECT + f" WHERE e.id IN ({placeholders})",  # nosec B608
                    batch,
                )
            )
        expenses = self._expenses_from_rows(item_rows)
        by_id = {expense.id: expense for expense in expenses}
        slots_by_laptop: dict[int, list[LaptopSlot]] = {
            int(row["item_id"]): [] for row in laptop_rows
        }
        for row in slot_rows:
            slots_by_laptop[int(row["laptop_id"])].append(
                LaptopSlot(
                    component_type=cast(LaptopComponentType, row["component_type"]),
                    slot_number=int(row["slot_number"]),
                    extracted=by_id[int(row["extracted_item_id"])],
                    installed=(
                        by_id[int(row["installed_item_id"])]
                        if row["installed_item_id"] is not None
                        else None
                    ),
                )
            )
        return tuple(
            Laptop(
                item=by_id[int(row["item_id"])],
                original_cost_cents=int(row["cash_paid_cents"]),
                slots=tuple(slots_by_laptop[int(row["item_id"])]),
            )
            for row in laptop_rows
        )

    def list_sales(self) -> tuple[Sale, ...]:
        sales = self.connection.execute(
            """SELECT s.id,s.name,
                      CASE WHEN ls.sale_id IS NULL THEN s.kind ELSE 'laptop' END AS kind,
                      s.selling_price_cents,s.sale_date
                 FROM sales s LEFT JOIN laptop_sales ls ON ls.sale_id=s.id ORDER BY s.id"""
        ).fetchall()
        items_by_sale: dict[int, list[Expense]] = {
            int(sale["id"]): [] for sale in sales
        }
        rows = self.connection.execute(
            EXPENSE_SELECT
            + " WHERE si.sale_id IS NOT NULL ORDER BY si.sale_id,si.position"
        )
        for expense in self._expenses_from_rows(rows):
            if expense.sale_id is not None:
                items_by_sale[expense.sale_id].append(expense)
        return tuple(
            Sale(
                id=sale["id"],
                name=sale["name"],
                kind=cast(SaleKind, sale["kind"]),
                cost_cents=sum(item.price_cents for item in items_by_sale[sale["id"]]),
                selling_price_cents=sale["selling_price_cents"],
                sale_date=date.fromisoformat(sale["sale_date"]),
                items=tuple(items_by_sale[sale["id"]]),
            )
            for sale in sales
        )

    def count_sales(self, search: str = "") -> int:
        if not search:
            return int(
                self.connection.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
            )
        return int(
            self.connection.execute(
                """SELECT COUNT(*) FROM sales s
                   LEFT JOIN laptop_sales ls ON ls.sale_id=s.id
                   WHERE PCIMS_CONTAINS(s.name,?) OR PCIMS_CONTAINS(
                         CASE WHEN ls.sale_id IS NULL THEN s.kind ELSE 'laptop' END,
                         ?)""",
                (search, search),
            ).fetchone()[0]
        )

    def list_sale_page(
        self,
        offset: int,
        limit: int,
        search: str = "",
    ) -> tuple[SaleSummary, ...]:
        sales = self.connection.execute(
            """WITH page AS (
                   SELECT s.id,s.name,
                          CASE WHEN ls.sale_id IS NULL THEN s.kind ELSE 'laptop' END AS kind,
                          s.selling_price_cents,s.sale_date
                     FROM sales s LEFT JOIN laptop_sales ls ON ls.sale_id=s.id
                    WHERE ?='' OR PCIMS_CONTAINS(s.name,?) OR PCIMS_CONTAINS(
                          CASE WHEN ls.sale_id IS NULL THEN s.kind ELSE 'laptop' END,
                          ?)
                    ORDER BY id DESC LIMIT ? OFFSET ?
               )
               SELECT p.id,p.name,p.kind,p.selling_price_cents,p.sale_date,
                      COUNT(si.item_id) AS item_count,
                      COALESCE(SUM(e.price_cents),0) AS cost_cents
                 FROM page p
                 JOIN sale_items si ON si.sale_id=p.id
                 JOIN inventory_items e ON e.id=si.item_id
                GROUP BY p.id,p.name,p.kind,p.selling_price_cents,p.sale_date
                ORDER BY p.id DESC""",
            (search, search, search, limit, offset),
        )
        return tuple(
            SaleSummary(
                id=sale["id"],
                name=sale["name"],
                kind=cast(SaleKind, sale["kind"]),
                cost_cents=sale["cost_cents"],
                selling_price_cents=sale["selling_price_cents"],
                sale_date=date.fromisoformat(sale["sale_date"]),
                item_count=sale["item_count"],
            )
            for sale in sales
        )

    def count_sale_items(self, sale_id: int) -> int:
        row = self.connection.execute(
            """SELECT COUNT(si.item_id)
                 FROM sales s LEFT JOIN sale_items si ON si.sale_id=s.id
                WHERE s.id=? GROUP BY s.id""",
            (sale_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"Sale {sale_id} does not exist.")
        return int(row[0])

    def list_sale_item_page(
        self, sale_id: int, offset: int, limit: int
    ) -> tuple[Expense, ...]:
        rows = self.connection.execute(
            EXPENSE_SELECT
            + " WHERE si.sale_id=? ORDER BY si.position LIMIT ? OFFSET ?",
            (sale_id, limit, offset),
        )
        return self._expenses_from_rows(rows)

    def proof_file(self, expense_id: int, proof_id: int) -> NewProof:
        row = self.connection.execute(
            """SELECT ip.file_name,pf.media_type,pf.content,pf.sha256
                 FROM item_proofs ip
                 JOIN proof_files pf ON pf.id=ip.proof_id
                WHERE ip.item_id=? AND ip.proof_id=?""",
            (expense_id, proof_id),
        ).fetchone()
        if row is None:
            raise NotFoundError(
                f"Proof {proof_id} is not attached to item {expense_id}."
            )
        content = bytes(row["content"])
        if hashlib.sha256(content).hexdigest() != row["sha256"]:
            raise DatabaseIntegrityError(
                f"Proof {proof_id} failed its content hash check."
            )
        try:
            return NewProof(row["file_name"], row["media_type"], content)
        except (TypeError, ValueError) as error:
            raise DatabaseIntegrityError(f"Proof {proof_id} is invalid.") from error

    def financial_summary(self) -> FinancialSummary:
        expense_cents, income_cents, cost_cents, inventory_cents = (
            self.connection.execute(
                """SELECT
                   (SELECT COALESCE(SUM(cash_paid_cents),0) FROM item_costs),
                   (SELECT COALESCE(SUM(selling_price_cents),0) FROM sales),
                   (SELECT COALESCE(SUM(e.price_cents),0)
                      FROM sale_items si JOIN inventory_items e ON e.id=si.item_id),
                   (SELECT COALESCE(SUM(e.price_cents),0) FROM inventory_items e
                      LEFT JOIN sale_items si ON si.item_id=e.id
                     WHERE si.sale_id IS NULL)"""
            ).fetchone()
        )
        return FinancialSummary(
            expense_cents=expense_cents,
            income_cents=income_cents,
            profit_cents=income_cents - cost_cents,
            inventory_cents=inventory_cents,
        )

    def balance_date_bounds(self) -> tuple[date | None, date | None]:
        row = self.connection.execute(
            """SELECT MIN(event_date),MAX(event_date) FROM (
                   SELECT e.purchase_date AS event_date FROM inventory_items e
                   JOIN item_costs c ON c.item_id=e.id WHERE c.origin='purchase'
                   UNION ALL SELECT sale_date FROM sales
               )"""
        ).fetchone()
        earliest = date.fromisoformat(row[0]) if row[0] is not None else None
        latest = date.fromisoformat(row[1]) if row[1] is not None else None
        return earliest, latest

    def balance_series(
        self,
        start_date: date,
        end_date: date,
        bucket: BalanceBucket,
    ) -> tuple[BalanceSummary, tuple[BalancePoint, ...]]:
        """Aggregate purchases and realized sales without loading raw history."""

        start_text = start_date.isoformat()
        end_text = end_date.isoformat()
        rows = self.connection.execute(
            """WITH sale_totals AS (
                   SELECT s.id,s.sale_date AS event_date,s.selling_price_cents,
                          SUM(i.price_cents) AS realized_cost_cents,
                          COUNT(si.item_id) AS sold_item_count
                     FROM sales s
                     JOIN sale_items si ON si.sale_id=s.id
                     JOIN inventory_items i ON i.id=si.item_id
                    WHERE s.sale_date BETWEEN ? AND ?
                    GROUP BY s.id,s.sale_date,s.selling_price_cents
               ), events AS (
                   SELECT e.purchase_date AS event_date,c.cash_paid_cents AS purchase_cents,
                          0 AS revenue_cents,0 AS realized_cost_cents,
                          1 AS purchase_count,0 AS sale_count,0 AS sold_item_count
                     FROM inventory_items e JOIN item_costs c ON c.item_id=e.id
                    WHERE c.origin='purchase' AND e.purchase_date BETWEEN ? AND ?
                   UNION ALL
                   SELECT event_date,0,selling_price_cents,realized_cost_cents,
                          0,1,sold_item_count FROM sale_totals
               ), bucketed AS (
                   SELECT CASE ?
                            WHEN 'day' THEN event_date
                            WHEN 'week' THEN date(
                                event_date,
                                '-' || ((CAST(strftime('%w',event_date) AS INTEGER)+6)%7)
                                || ' days')
                            WHEN 'month' THEN substr(event_date,1,7) || '-01'
                            ELSE substr(event_date,1,4) || '-01-01'
                          END AS period_start,
                          SUM(purchase_cents) AS purchase_cents,
                          SUM(revenue_cents) AS revenue_cents,
                          SUM(realized_cost_cents) AS realized_cost_cents,
                          SUM(purchase_count) AS purchase_count,
                          SUM(sale_count) AS sale_count,
                          SUM(sold_item_count) AS sold_item_count
                     FROM events GROUP BY period_start
               )
               SELECT 0 AS row_kind,NULL AS period_start,
                      COALESCE(SUM(purchase_cents),0) AS purchase_cents,
                      COALESCE(SUM(revenue_cents),0) AS revenue_cents,
                      COALESCE(SUM(realized_cost_cents),0) AS realized_cost_cents,
                      COALESCE(SUM(purchase_count),0) AS purchase_count,
                      COALESCE(SUM(sale_count),0) AS sale_count,
                      COALESCE(SUM(sold_item_count),0) AS sold_item_count,
                      (SELECT COALESCE(SUM(i.price_cents),0)
                         FROM inventory_items i LEFT JOIN sale_items si
                           ON si.item_id=i.id WHERE si.sale_id IS NULL)
                          AS current_inventory_cents
                 FROM events
               UNION ALL
               SELECT 1,period_start,purchase_cents,revenue_cents,
                      realized_cost_cents,purchase_count,sale_count,sold_item_count,0
                 FROM bucketed
                ORDER BY row_kind,period_start""",
            (start_text, end_text, start_text, end_text, bucket),
        ).fetchall()
        summary_row = rows[0]
        summary = BalanceSummary(
            purchase_cents=int(summary_row["purchase_cents"]),
            revenue_cents=int(summary_row["revenue_cents"]),
            realized_cost_cents=int(summary_row["realized_cost_cents"]),
            current_inventory_cents=int(summary_row["current_inventory_cents"]),
            purchase_count=int(summary_row["purchase_count"]),
            sale_count=int(summary_row["sale_count"]),
            sold_item_count=int(summary_row["sold_item_count"]),
        )
        points = tuple(
            BalancePoint(
                period_start=date.fromisoformat(row["period_start"]),
                purchase_cents=int(row["purchase_cents"]),
                revenue_cents=int(row["revenue_cents"]),
                realized_cost_cents=int(row["realized_cost_cents"]),
                purchase_count=int(row["purchase_count"]),
                sale_count=int(row["sale_count"]),
                sold_item_count=int(row["sold_item_count"]),
            )
            for row in rows[1:]
        )
        return summary, points
