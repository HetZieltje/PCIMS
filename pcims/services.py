"""Application-facing service boundary over persistence and recovery."""

import os
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from pcims.contracts import (
    AssembleSnapshot,
    BackupResult,
    BalanceSnapshot,
    HistoryPage,
    InventorySnapshot,
    PurchasesSnapshot,
    RestoreResult,
    SalesSnapshot,
    StorageSummary,
)
from pcims.db.assembly_commands import (
    assemble_pc,
    disassemble_pc,
    update_pc,
)
from pcims.db.connection import Database, default_database
from pcims.db.expense_commands import (
    add_expenses,
    delete_expenses,
    replace_expense_proofs,
    update_expense,
)
from pcims.db.reads import ReadQueries
from pcims.db.sale_commands import (
    sell_items,
    sell_pc,
    undo_sale,
    update_sale,
)
from pcims.db.schema import initialize_database
from pcims.domain import ItemType, NewExpense, SaleTerms
from pcims.models import (
    AssembledPC,
    BalanceBucket,
    BalancePoint,
    Expense,
    FinancialSummary,
    Sale,
)
from pcims.proofs import NewProof

MAX_HISTORY_PAGE_SIZE = 1_000


@dataclass(frozen=True, slots=True)
class ApplicationServices:
    """All technical operations available to the Qt presentation layer."""

    database: Database

    def initialize(self) -> None:
        initialize_database(self.database)

    def inventory_snapshot(self) -> InventorySnapshot:
        with self.database.transaction() as connection:
            queries = ReadQueries(connection)
            return InventorySnapshot(queries.list_inventory(), queries.list_pcs())

    def purchases_snapshot(self) -> PurchasesSnapshot:
        with self.database.transaction() as connection:
            names = ReadQueries(connection).list_expense_names()
        return PurchasesSnapshot(names)

    def assemble_snapshot(self) -> AssembleSnapshot:
        with self.database.transaction() as connection:
            queries = ReadQueries(connection)
            inventory = queries.list_inventory(available_only=True)
            pc_names = tuple(pc.name for pc in queries.list_pcs())
        return AssembleSnapshot(inventory, pc_names)

    def sales_snapshot(
        self,
        expense_offset: int = 0,
        sale_offset: int = 0,
        page_size: int = 500,
        search: str = "",
    ) -> SalesSnapshot:
        page_size = _history_page_size(page_size)
        expense_offset = _history_offset(expense_offset)
        sale_offset = _history_offset(sale_offset)
        search = str(search).strip()[:200]
        with self.database.transaction() as connection:
            queries = ReadQueries(connection)
            expense_total = queries.count_expenses(search)
            sale_total = queries.count_sales(search)
            expense_offset = _clamped_page_offset(
                expense_offset, expense_total, page_size
            )
            sale_offset = _clamped_page_offset(sale_offset, sale_total, page_size)
            expenses = queries.list_expense_page(expense_offset, page_size, search)
            return SalesSnapshot(
                queries.financial_summary(),
                HistoryPage(
                    expenses,
                    expense_offset,
                    expense_total,
                    page_size,
                ),
                HistoryPage(
                    queries.list_sale_page(sale_offset, page_size, search),
                    sale_offset,
                    sale_total,
                    page_size,
                ),
            )

    def sale_item_page(
        self,
        sale_id: int,
        offset: int = 0,
        page_size: int = 500,
    ) -> HistoryPage[Expense]:
        if isinstance(sale_id, bool) or not isinstance(sale_id, int) or sale_id < 1:
            raise ValueError("Sale ID must be positive.")
        page_size = _history_page_size(page_size)
        offset = _history_offset(offset)
        with self.database.transaction() as connection:
            queries = ReadQueries(connection)
            total = queries.count_sale_items(sale_id)
            offset = _clamped_page_offset(offset, total, page_size)
            records = queries.list_sale_item_page(sale_id, offset, page_size)
        return HistoryPage(records, offset, total, page_size)

    def add_expenses(
        self,
        items: Iterable[NewExpense],
        proofs_by_item: Iterable[Iterable[NewProof]] | None = None,
    ) -> list[int]:
        return add_expenses(items, proofs_by_item, database=self.database)

    def list_expenses(self) -> tuple[Expense, ...]:
        with self.database.transaction() as connection:
            return ReadQueries(connection).list_expenses()

    def list_inventory(
        self, item_type: ItemType | None = None, available_only: bool = False
    ) -> tuple[Expense, ...]:
        with self.database.transaction() as connection:
            return ReadQueries(connection).list_inventory(item_type, available_only)

    def delete_expenses(self, expense_ids: Iterable[int]) -> None:
        delete_expenses(expense_ids, database=self.database)

    def update_expense(self, expense_id: int, replacement: NewExpense) -> None:
        update_expense(expense_id, replacement, database=self.database)

    def replace_expense_proofs(
        self,
        expense_id: int,
        retained_proof_ids: Iterable[int],
        new_proofs: Iterable[NewProof],
    ) -> None:
        replace_expense_proofs(
            expense_id,
            retained_proof_ids,
            new_proofs,
            database=self.database,
        )

    def proof_file(self, expense_id: int, proof_id: int) -> NewProof:
        with self.database.transaction() as connection:
            return ReadQueries(connection).proof_file(expense_id, proof_id)

    def assemble_pc(self, name: str, expense_ids: Iterable[int]) -> int:
        return assemble_pc(name, expense_ids, database=self.database)

    def list_pcs(self) -> tuple[AssembledPC, ...]:
        with self.database.transaction() as connection:
            return ReadQueries(connection).list_pcs()

    def disassemble_pc(self, pc_id: int) -> None:
        disassemble_pc(pc_id, database=self.database)

    def update_pc(self, pc_id: int, name: str, expense_ids: Iterable[int]) -> None:
        update_pc(pc_id, name, expense_ids, database=self.database)

    def sell_items(
        self,
        expense_ids: Iterable[int],
        terms: SaleTerms,
    ) -> int:
        return sell_items(expense_ids, terms, database=self.database)

    def sell_pc(
        self,
        pc_id: int,
        terms: SaleTerms,
    ) -> int:
        return sell_pc(pc_id, terms, database=self.database)

    def list_sales(self) -> tuple[Sale, ...]:
        with self.database.transaction() as connection:
            return ReadQueries(connection).list_sales()

    def undo_sale(self, sale_id: int) -> None:
        undo_sale(sale_id, database=self.database)

    def update_sale(self, sale_id: int, terms: SaleTerms) -> None:
        update_sale(sale_id, terms, database=self.database)

    def financial_summary(self) -> FinancialSummary:
        with self.database.transaction() as connection:
            return ReadQueries(connection).financial_summary()

    def balance_snapshot(
        self,
        start_date: date | None,
        end_date: date,
    ) -> BalanceSnapshot:
        if start_date is not None and type(start_date) is not date:
            raise TypeError("Dashboard start date must be a date or None.")
        if type(end_date) is not date:
            raise TypeError("Dashboard end date must be a date.")
        with self.database.transaction() as connection:
            queries = ReadQueries(connection)
            if start_date is None:
                earliest, latest = queries.balance_date_bounds()
                start_date = earliest or end_date
                if latest is not None:
                    end_date = max(end_date, latest)
            if start_date > end_date:
                raise ValueError("Dashboard start date cannot be after its end date.")
            bucket = _balance_bucket(start_date, end_date)
            summary, sparse_points = queries.balance_series(
                start_date, end_date, bucket
            )
        return BalanceSnapshot(
            start_date,
            end_date,
            bucket,
            summary,
            _fill_balance_points(start_date, end_date, bucket, sparse_points),
        )

    def export_csv(self, directory: str | os.PathLike[str]) -> tuple[Path, Path]:
        from pcims.db.export import export_csv

        return export_csv(directory, database=self.database)

    def create_backup(
        self,
        destination_directory: str | os.PathLike[str] | None = None,
        keep: int = 14,
    ) -> BackupResult:
        from pcims.db.backup import create_backup

        return create_backup(destination_directory, keep, database=self.database)

    def storage_summary(self) -> StorageSummary:
        from pcims.db.backup import backup_usage

        with self.database.transaction() as connection:
            proof_count, proof_bytes = connection.execute(
                "SELECT COUNT(*),COALESCE(SUM(length(content)),0) FROM proof_files"
            ).fetchone()
        database_bytes = 0
        for path in (
            self.database.path,
            Path(f"{self.database.path}-wal"),
            Path(f"{self.database.path}-shm"),
        ):
            try:
                database_bytes += path.stat().st_size
            except OSError:
                continue
        backup_count, backup_bytes = backup_usage(database=self.database)
        return StorageSummary(
            database_bytes=database_bytes,
            proof_bytes=int(proof_bytes),
            proof_count=int(proof_count),
            backup_bytes=backup_bytes,
            backup_count=backup_count,
        )

    def restore_backup(
        self,
        backup_path: str | os.PathLike[str],
        pre_restore_directory: str | os.PathLike[str] | None = None,
        keep: int = 14,
    ) -> RestoreResult:
        from pcims.db.backup import restore_backup

        result = restore_backup(
            backup_path, pre_restore_directory, keep, database=self.database
        )
        initialize_database(self.database)
        return result

    @property
    def database_path(self) -> Path:
        return self.database.path


def default_services() -> ApplicationServices:
    """Build services once at the application composition root."""
    return ApplicationServices(default_database())


def _clamped_page_offset(offset: int, total: int, limit: int) -> int:
    if total == 0:
        return 0
    return min(offset, ((total - 1) // limit) * limit)


def _history_page_size(value: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= MAX_HISTORY_PAGE_SIZE
    ):
        raise ValueError(
            f"History page size must be between 1 and {MAX_HISTORY_PAGE_SIZE}."
        )
    return value


def _history_offset(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("History offsets must be non-negative integers.")
    return value


def _balance_bucket(start_date: date, end_date: date) -> BalanceBucket:
    day_count = (end_date - start_date).days + 1
    if day_count <= 45:
        return "day"
    if day_count <= 210:
        return "week"
    if day_count <= 365 * 6:
        return "month"
    return "year"


def _bucket_start(value: date, bucket: BalanceBucket) -> date:
    if bucket == "day":
        return value
    if bucket == "week":
        return value - timedelta(days=value.weekday())
    if bucket == "month":
        return value.replace(day=1)
    return value.replace(month=1, day=1)


def _next_bucket(value: date, bucket: BalanceBucket) -> date:
    if bucket == "day":
        return value + timedelta(days=1)
    if bucket == "week":
        return value + timedelta(days=7)
    if bucket == "month":
        return (
            value.replace(year=value.year + 1, month=1)
            if value.month == 12
            else value.replace(month=value.month + 1)
        )
    return value.replace(year=value.year + 1)


def _fill_balance_points(
    start_date: date,
    end_date: date,
    bucket: BalanceBucket,
    sparse_points: tuple[BalancePoint, ...],
) -> tuple[BalancePoint, ...]:
    by_start = {point.period_start: point for point in sparse_points}
    result: list[BalancePoint] = []
    current = _bucket_start(start_date, bucket)
    final = _bucket_start(end_date, bucket)
    while current <= final:
        result.append(
            by_start.get(
                current,
                BalancePoint(current, 0, 0, 0, 0, 0, 0),
            )
        )
        if len(result) >= 240 and current < final:
            return sparse_points
        if current == final:
            break
        current = _next_bucket(current, bucket)
    return tuple(result)
