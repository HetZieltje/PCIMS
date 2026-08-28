"""Laptop inventory application services."""

from collections.abc import Iterable

from pcims.contracts import LaptopSnapshot
from pcims.db.connection import Database
from pcims.db.laptop_commands import (
    add_laptop,
    delete_laptop,
    extract_laptop_component,
    restore_laptop_component,
    sell_laptop,
    set_laptop_replacement,
    update_laptop,
)
from pcims.db.reads import ReadQueries
from pcims.domain import LaptopSlotRef, NewExpense, SaleTerms
from pcims.proofs import NewProof


class LaptopServices:
    database: Database

    def laptop_snapshot(self) -> LaptopSnapshot:
        with self.database.transaction() as connection:
            queries = ReadQueries(connection)
            return LaptopSnapshot(
                queries.list_laptops(),
                queries.list_inventory(available_only=True),
            )

    def add_laptop(self, laptop: NewExpense, proofs: Iterable[NewProof] = ()) -> int:
        return add_laptop(laptop, proofs, database=self.database)

    def update_laptop(self, laptop_id: int, replacement: NewExpense) -> None:
        update_laptop(laptop_id, replacement, database=self.database)

    def extract_laptop_component(
        self,
        laptop_id: int,
        component_type: str,
        slot_number: int,
        extracted: NewExpense,
        installed_item_id: int | None = None,
    ) -> int:
        return extract_laptop_component(
            laptop_id,
            LaptopSlotRef.create(component_type, slot_number),
            extracted,
            installed_item_id,
            database=self.database,
        )

    def set_laptop_replacement(
        self,
        laptop_id: int,
        component_type: str,
        slot_number: int,
        installed_item_id: int | None,
    ) -> None:
        set_laptop_replacement(
            laptop_id,
            LaptopSlotRef.create(component_type, slot_number),
            installed_item_id,
            database=self.database,
        )

    def restore_laptop_component(
        self, laptop_id: int, component_type: str, slot_number: int
    ) -> None:
        restore_laptop_component(
            laptop_id,
            LaptopSlotRef.create(component_type, slot_number),
            database=self.database,
        )

    def delete_laptop(self, laptop_id: int) -> None:
        delete_laptop(laptop_id, database=self.database)

    def sell_laptop(self, laptop_id: int, terms: SaleTerms) -> int:
        return sell_laptop(laptop_id, terms, database=self.database)
