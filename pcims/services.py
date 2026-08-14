"""Application-facing service boundary over persistence and recovery."""

import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from pcims.contracts import (
    AssembleSnapshot,
    BackupResult,
    InventorySnapshot,
    PurchasesSnapshot,
    RestoreResult,
    SalesSnapshot,
)
from pcims.db.assembly_commands import (
    assemble_pc,
    disassemble_pc,
    rename_pc,
)
from pcims.db.backup import create_backup, restore_backup
from pcims.db.connection import Database, default_database
from pcims.db.expense_commands import (
    add_expenses,
    delete_expenses,
    rename_expenses,
)
from pcims.db.reads import ReadQueries
from pcims.db.sale_commands import (
    sell_items,
    sell_pc,
    undo_sale,
)
from pcims.db.schema import initialize_database
from pcims.domain import ItemType, NewExpense, SaleTerms
from pcims.models import AssembledPC, Expense, FinancialSummary, Sale


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

    def sales_snapshot(self) -> SalesSnapshot:
        with self.database.transaction() as connection:
            queries = ReadQueries(connection)
            expenses = queries.list_expenses()
            return SalesSnapshot(
                queries.financial_summary(),
                expenses,
                queries.list_sales({expense.id: expense for expense in expenses}),
            )

    def add_expenses(self, items: Iterable[NewExpense]) -> list[int]:
        return add_expenses(items, database=self.database)

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

    def rename_expenses(
        self, expense_ids: Iterable[int], new_name: str
    ) -> None:
        rename_expenses(expense_ids, new_name, database=self.database)

    def assemble_pc(self, name: str, expense_ids: Iterable[int]) -> int:
        return assemble_pc(name, expense_ids, database=self.database)

    def list_pcs(self) -> tuple[AssembledPC, ...]:
        with self.database.transaction() as connection:
            return ReadQueries(connection).list_pcs()

    def disassemble_pc(self, pc_id: int) -> None:
        disassemble_pc(pc_id, database=self.database)

    def rename_pc(self, pc_id: int, new_name: str) -> None:
        rename_pc(pc_id, new_name, database=self.database)

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

    def financial_summary(self) -> FinancialSummary:
        with self.database.transaction() as connection:
            return ReadQueries(connection).financial_summary()

    def create_backup(
        self,
        destination_directory: str | os.PathLike[str] | None = None,
        keep: int = 14,
    ) -> BackupResult:
        return create_backup(destination_directory, keep, database=self.database)

    def restore_backup(
        self,
        backup_path: str | os.PathLike[str],
        pre_restore_directory: str | os.PathLike[str] | None = None,
    ) -> RestoreResult:
        return restore_backup(
            backup_path, pre_restore_directory, database=self.database
        )

    @property
    def database_path(self) -> Path:
        return self.database.path


def default_services() -> ApplicationServices:
    """Build services once at the application composition root."""
    return ApplicationServices(default_database())
