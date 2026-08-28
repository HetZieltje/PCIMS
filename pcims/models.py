"""Immutable application records independent from storage and presentation."""

from dataclasses import dataclass, field
from datetime import date
from typing import Literal, TypeAlias

from pcims.domain import ItemDetails, ItemType, LaptopComponentType, SaleKind
from pcims.lifecycle import InventoryState, ItemPlacement
from pcims.proofs import ProofSummary

BalanceBucket: TypeAlias = Literal["day", "week", "month", "year"]


def _ratio_basis_points(numerator_cents: int, denominator_cents: int) -> int | None:
    """Return a ratio as hundredths of a percentage point."""
    if denominator_cents == 0:
        return None
    numerator = numerator_cents * 10_000
    if numerator >= 0:
        return (numerator + denominator_cents // 2) // denominator_cents
    return -((-numerator + denominator_cents // 2) // denominator_cents)


@dataclass(frozen=True, slots=True)
class Expense:
    id: int
    name: str
    item_type: ItemType
    price_cents: int
    purchase_date: date
    cash_paid_cents: int | None = None
    cost_origin: Literal["purchase", "extracted"] = "purchase"
    pc_id: int | None = None
    pc_name: str | None = None
    laptop_id: int | None = None
    laptop_name: str | None = None
    is_laptop: bool = False
    sale_id: int | None = None
    proofs: tuple[ProofSummary, ...] = ()
    details: ItemDetails = field(default_factory=ItemDetails)

    @property
    def is_available(self) -> bool:
        return self.lifecycle_state is InventoryState.AVAILABLE

    @property
    def placement(self) -> ItemPlacement:
        return ItemPlacement(
            pc_id=self.pc_id,
            laptop_id=self.laptop_id,
            is_laptop=self.is_laptop,
            sale_id=self.sale_id,
        )

    @property
    def lifecycle_state(self) -> InventoryState:
        return self.placement.state

    @property
    def purchase_cost_cents(self) -> int:
        return (
            self.price_cents if self.cash_paid_cents is None else self.cash_paid_cents
        )

    @property
    def display_type(self) -> str:
        return "Laptop" if self.is_laptop else self.item_type


@dataclass(frozen=True, slots=True)
class LaptopSlot:
    component_type: LaptopComponentType
    slot_number: int
    extracted: Expense
    installed: Expense | None = None


@dataclass(frozen=True, slots=True)
class Laptop:
    item: Expense
    original_cost_cents: int
    slots: tuple[LaptopSlot, ...] = ()

    @property
    def id(self) -> int:
        return self.item.id

    @property
    def name(self) -> str:
        return self.item.name

    @property
    def current_cost_cents(self) -> int:
        return self.item.price_cents + sum(
            slot.installed.price_cents
            for slot in self.slots
            if slot.installed is not None
        )

    @property
    def is_sold(self) -> bool:
        return self.item.sale_id is not None


@dataclass(frozen=True, slots=True)
class AssembledPC:
    id: int
    name: str
    parts: tuple[Expense, ...]

    @property
    def cost_cents(self) -> int:
        return sum(part.price_cents for part in self.parts)


@dataclass(frozen=True, slots=True)
class Sale:
    id: int
    name: str
    kind: SaleKind
    cost_cents: int
    selling_price_cents: int
    sale_date: date
    items: tuple[Expense, ...]

    @property
    def profit_cents(self) -> int:
        return self.selling_price_cents - self.cost_cents

    @property
    def roi_basis_points(self) -> int | None:
        return _ratio_basis_points(self.profit_cents, self.cost_cents)


@dataclass(frozen=True, slots=True)
class SaleSummary:
    """One sale-list row without its potentially large item collection."""

    id: int
    name: str
    kind: SaleKind
    cost_cents: int
    selling_price_cents: int
    sale_date: date
    item_count: int

    @property
    def profit_cents(self) -> int:
        return self.selling_price_cents - self.cost_cents

    @property
    def roi_basis_points(self) -> int | None:
        return _ratio_basis_points(self.profit_cents, self.cost_cents)


@dataclass(frozen=True, slots=True)
class FinancialSummary:
    expense_cents: int
    income_cents: int
    profit_cents: int
    inventory_cents: int

    @property
    def cash_flow_cents(self) -> int:
        return self.income_cents - self.expense_cents

    @property
    def realized_cost_cents(self) -> int:
        return self.income_cents - self.profit_cents

    @property
    def roi_basis_points(self) -> int | None:
        return _ratio_basis_points(self.profit_cents, self.realized_cost_cents)


@dataclass(frozen=True, slots=True)
class BalancePoint:
    """Economic activity grouped into one dashboard time bucket."""

    period_start: date
    purchase_cents: int
    revenue_cents: int
    realized_cost_cents: int
    purchase_count: int
    sale_count: int
    sold_item_count: int

    @property
    def profit_cents(self) -> int:
        return self.revenue_cents - self.realized_cost_cents

    @property
    def cash_flow_cents(self) -> int:
        return self.revenue_cents - self.purchase_cents

    @property
    def roi_basis_points(self) -> int | None:
        return _ratio_basis_points(self.profit_cents, self.realized_cost_cents)


@dataclass(frozen=True, slots=True)
class BalanceSummary:
    """Selected-period totals plus the current unsold inventory valuation."""

    purchase_cents: int
    revenue_cents: int
    realized_cost_cents: int
    current_inventory_cents: int
    purchase_count: int
    sale_count: int
    sold_item_count: int

    @property
    def profit_cents(self) -> int:
        return self.revenue_cents - self.realized_cost_cents

    @property
    def cash_flow_cents(self) -> int:
        return self.revenue_cents - self.purchase_cents

    @property
    def roi_basis_points(self) -> int | None:
        return _ratio_basis_points(self.profit_cents, self.realized_cost_cents)

    @property
    def profit_margin_basis_points(self) -> int | None:
        return _ratio_basis_points(self.profit_cents, self.revenue_cents)

    @property
    def average_sale_cents(self) -> int | None:
        if self.sale_count == 0:
            return None
        return (self.revenue_cents + self.sale_count // 2) // self.sale_count
