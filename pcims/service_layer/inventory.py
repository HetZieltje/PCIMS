"""Purchase, inventory, proof, and assembled-PC application services."""

from collections.abc import Iterable
from pathlib import Path

from pcims.contracts import AssembleSnapshot, InventorySnapshot, PurchasesSnapshot
from pcims.db.assembly_commands import assemble_pc, disassemble_pc, update_pc
from pcims.db.connection import Database
from pcims.db.expense_commands import (
    add_expenses,
    delete_expenses,
    replace_expense_proofs,
    update_expense,
)
from pcims.db.reads import ReadQueries
from pcims.domain import ItemType, NewExpense
from pcims.models import AssembledPC, Expense
from pcims.proofs import NewProof


class InventoryServices:
    database: Database

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

    @property
    def database_path(self) -> Path:
        return self.database.path
