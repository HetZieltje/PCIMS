"""Narrow, presentation-facing capability contracts and coherent snapshots."""

import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, Protocol, TypeVar

from pcims.domain import NewExpense, SaleTerms
from pcims.models import AssembledPC, AuditEvent, Expense, FinancialSummary, SaleSummary
from pcims.proofs import NewProof

RecordT = TypeVar("RecordT")


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
class HistoryPage(Generic[RecordT]):
    records: tuple[RecordT, ...]
    offset: int
    total: int
    limit: int

    @property
    def has_previous(self) -> bool:
        return self.offset > 0

    @property
    def has_next(self) -> bool:
        return self.offset + len(self.records) < self.total


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
    expenses: HistoryPage[Expense]
    sales: HistoryPage[SaleSummary]


class InventoryOperations(Protocol):
    def inventory_snapshot(self) -> InventorySnapshot: ...
    def delete_expenses(self, expense_ids: Iterable[int]) -> None: ...
    def update_expense(self, expense_id: int, replacement: NewExpense) -> None: ...
    def replace_expense_proofs(
        self,
        expense_id: int,
        retained_proof_ids: Iterable[int],
        new_proofs: Iterable[NewProof],
    ) -> None: ...
    def proof_file(self, expense_id: int, proof_id: int) -> NewProof: ...
    def disassemble_pc(self, pc_id: int) -> None: ...
    def update_pc(self, pc_id: int, name: str, expense_ids: Iterable[int]) -> None: ...
    def sell_items(self, expense_ids: Iterable[int], terms: SaleTerms) -> int: ...
    def sell_pc(self, pc_id: int, terms: SaleTerms) -> int: ...


class PurchaseOperations(Protocol):
    @property
    def database_path(self) -> Path: ...

    def purchases_snapshot(self) -> PurchasesSnapshot: ...
    def add_expenses(
        self,
        items: Iterable[NewExpense],
        proofs_by_item: Iterable[Iterable[NewProof]] | None = None,
    ) -> list[int]: ...


class AssemblyOperations(Protocol):
    def assemble_snapshot(self) -> AssembleSnapshot: ...
    def assemble_pc(self, name: str, expense_ids: Iterable[int]) -> int: ...


class SalesOperations(Protocol):
    def sales_snapshot(
        self,
        expense_offset: int = 0,
        sale_offset: int = 0,
        page_size: int = 500,
        search: str = "",
    ) -> SalesSnapshot: ...
    def sale_item_page(
        self,
        sale_id: int,
        offset: int = 0,
        page_size: int = 500,
    ) -> HistoryPage[Expense]: ...
    def undo_sale(self, sale_id: int) -> None: ...
    def replace_expense_proofs(
        self,
        expense_id: int,
        retained_proof_ids: Iterable[int],
        new_proofs: Iterable[NewProof],
    ) -> None: ...
    def proof_file(self, expense_id: int, proof_id: int) -> NewProof: ...


class ActivityOperations(Protocol):
    def list_activity(self, limit: int = 500) -> tuple[AuditEvent, ...]: ...
    def clear_activity(self) -> None: ...


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

    def export_csv(self, directory: str | os.PathLike[str]) -> tuple[Path, Path]: ...
