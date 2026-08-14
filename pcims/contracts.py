"""Narrow, presentation-facing capability contracts and coherent snapshots."""

import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pcims.domain import NewExpense, SaleTerms
from pcims.models import AssembledPC, Expense, FinancialSummary, Sale


@dataclass(frozen=True, slots=True)
class BackupResult(os.PathLike[str]):
    """A verified backup plus any non-fatal durability or retention warnings."""

    path: Path
    warnings: tuple[str, ...] = ()
    durable: bool = True

    def __fspath__(self) -> str:
        return str(self.path)

    def __str__(self) -> str:
        return str(self.path)

    @property
    def has_warnings(self) -> bool:
        return bool(self.warnings)

    @property
    def warning_text(self) -> str:
        return "\n".join(self.warnings)


@dataclass(frozen=True, slots=True)
class RestoreResult:
    """A completed restore, its rollback artifact, and publication status."""

    source_path: Path
    safety_backup: BackupResult
    warnings: tuple[str, ...] = ()
    durable: bool = True

    @property
    def all_warnings(self) -> tuple[str, ...]:
        return (*self.safety_backup.warnings, *self.warnings)

    @property
    def has_warnings(self) -> bool:
        return bool(self.all_warnings)

    @property
    def warning_text(self) -> str:
        return "\n".join(self.all_warnings)


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


class InventoryOperations(Protocol):
    def inventory_snapshot(self) -> InventorySnapshot: ...
    def delete_expenses(self, expense_ids: Iterable[int]) -> None: ...
    def rename_expenses(self, expense_ids: Iterable[int], new_name: str) -> None: ...
    def disassemble_pc(self, pc_id: int) -> None: ...
    def rename_pc(self, pc_id: int, new_name: str) -> None: ...
    def sell_items(self, expense_ids: Iterable[int], terms: SaleTerms) -> int: ...
    def sell_pc(self, pc_id: int, terms: SaleTerms) -> int: ...


class PurchaseOperations(Protocol):
    def purchases_snapshot(self) -> PurchasesSnapshot: ...
    def add_expenses(self, items: Iterable[NewExpense]) -> list[int]: ...


class AssemblyOperations(Protocol):
    def assemble_snapshot(self) -> AssembleSnapshot: ...
    def assemble_pc(self, name: str, expense_ids: Iterable[int]) -> int: ...


class SalesOperations(Protocol):
    def sales_snapshot(self) -> SalesSnapshot: ...
    def undo_sale(self, sale_id: int) -> None: ...


class MaintenanceOperations(Protocol):
    @property
    def database_path(self) -> Path: ...

    def create_backup(
        self,
        destination_directory: str | os.PathLike[str] | None = None,
        keep: int = 14,
    ) -> BackupResult: ...

    def restore_backup(
        self,
        backup_path: str | os.PathLike[str],
        pre_restore_directory: str | os.PathLike[str] | None = None,
    ) -> RestoreResult: ...
