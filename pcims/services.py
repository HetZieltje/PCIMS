"""Application-facing service boundary over persistence and recovery."""

import os
import threading
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from pcims.db.backup import BackupResult, create_backup, restore_backup
from pcims.db.connection import Database, get_database
from pcims.db.models import AssembledPC, Expense, FinancialSummary, Sale
from pcims.db.queries import (
    ReadQueries,
    add_expenses,
    assemble_pc,
    delete_expenses,
    disassemble_pc,
    get_financial_summary,
    list_expenses,
    list_inventory,
    list_pcs,
    list_sales,
    rename_expenses,
    rename_pc,
    sell_items,
    sell_pc,
    undo_sale,
)
from pcims.db.schema import initialize_database
from pcims.domain import PurchaseInput


@dataclass(frozen=True, slots=True)
class InventorySnapshot:
    inventory: tuple[Expense, ...]
    pcs: tuple[AssembledPC, ...]


@dataclass(frozen=True, slots=True)
class PurchasesSnapshot:
    expense_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AssembleSnapshot:
    available_inventory: tuple[Expense, ...]
    pc_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SalesSnapshot:
    summary: FinancialSummary
    expenses: tuple[Expense, ...]
    sales: tuple[Sale, ...]


@dataclass(frozen=True, slots=True)
class ApplicationServices:
    """All technical operations available to the Qt presentation layer."""

    database: Database
    _recovery_lock: threading.RLock = field(
        default_factory=threading.RLock, repr=False, compare=False
    )

    def initialize(self) -> None:
        initialize_database(self.database)

    def inventory_snapshot(self) -> InventorySnapshot:
        with self.database.transaction() as connection:
            queries = ReadQueries(connection)
            return InventorySnapshot(queries.list_inventory(), queries.list_pcs())

    def purchases_snapshot(self) -> PurchasesSnapshot:
        with self.database.transaction() as connection:
            names = {expense.name for expense in ReadQueries(connection).list_expenses()}
        return PurchasesSnapshot(tuple(sorted(names, key=str.casefold)))

    def assemble_snapshot(self) -> AssembleSnapshot:
        with self.database.transaction() as connection:
            queries = ReadQueries(connection)
            inventory = queries.list_inventory(available_only=True)
            pc_names = tuple(pc.name for pc in queries.list_pcs())
        return AssembleSnapshot(inventory, pc_names)

    def sales_snapshot(self) -> SalesSnapshot:
        with self.database.transaction() as connection:
            queries = ReadQueries(connection)
            return SalesSnapshot(
                queries.financial_summary(),
                queries.list_expenses(),
                queries.list_sales(),
            )

    def add_expenses(self, items: Iterable[PurchaseInput]) -> list[int]:
        return add_expenses(items, database=self.database)

    def list_expenses(self) -> tuple[Expense, ...]:
        return list_expenses(database=self.database)

    def list_inventory(
        self, item_type: object | None = None, available_only: bool = False
    ) -> tuple[Expense, ...]:
        return list_inventory(item_type, available_only, database=self.database)

    def delete_expenses(self, expense_ids: Iterable[object]) -> None:
        delete_expenses(expense_ids, database=self.database)

    def rename_expenses(
        self, expense_ids: Iterable[object], new_name: object
    ) -> None:
        rename_expenses(expense_ids, new_name, database=self.database)

    def assemble_pc(self, name: object, expense_ids: Iterable[object]) -> int:
        return assemble_pc(name, expense_ids, database=self.database)

    def list_pcs(self) -> tuple[AssembledPC, ...]:
        return list_pcs(database=self.database)

    def disassemble_pc(self, pc_id: object) -> None:
        disassemble_pc(pc_id, database=self.database)

    def rename_pc(self, pc_id: object, new_name: object) -> None:
        rename_pc(pc_id, new_name, database=self.database)

    def sell_items(
        self,
        expense_ids: Iterable[object],
        selling_price: object,
        sale_date: object | None = None,
    ) -> int:
        return sell_items(
            expense_ids, selling_price, sale_date, database=self.database
        )

    def sell_pc(
        self,
        pc_id: object,
        selling_price: object,
        sale_date: object | None = None,
    ) -> int:
        return sell_pc(pc_id, selling_price, sale_date, database=self.database)

    def list_sales(self) -> tuple[Sale, ...]:
        return list_sales(database=self.database)

    def undo_sale(self, sale_id: object) -> None:
        undo_sale(sale_id, database=self.database)

    def financial_summary(self) -> FinancialSummary:
        return get_financial_summary(database=self.database)

    def create_backup(
        self,
        destination_directory: str | os.PathLike[str] | None = None,
        keep: int = 14,
    ) -> BackupResult:
        with self._recovery_lock:
            return create_backup(
                destination_directory, keep, database=self.database
            )

    def restore_backup(
        self,
        backup_path: str | os.PathLike[str],
        pre_restore_directory: str | os.PathLike[str] | None = None,
    ) -> BackupResult:
        with self._recovery_lock:
            return restore_backup(
                backup_path, pre_restore_directory, database=self.database
            )

    @property
    def database_path(self) -> Path:
        return self.database.path


def default_services() -> ApplicationServices:
    """Build services once at the application composition root."""
    return ApplicationServices(get_database())
