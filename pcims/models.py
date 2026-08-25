"""Immutable application records independent from storage and presentation."""

from dataclasses import dataclass, field
from datetime import date

from pcims.domain import ItemDetails, ItemType, SaleKind
from pcims.proofs import ProofSummary


@dataclass(frozen=True, slots=True)
class Expense:
    id: int
    name: str
    item_type: ItemType
    price_cents: int
    purchase_date: date
    pc_id: int | None = None
    pc_name: str | None = None
    sale_id: int | None = None
    proofs: tuple[ProofSummary, ...] = ()
    details: ItemDetails = field(default_factory=ItemDetails)

    @property
    def is_available(self) -> bool:
        return self.pc_id is None and self.sale_id is None


@dataclass(frozen=True, slots=True)
class AuditEvent:
    id: int
    occurred_at: str
    action: str
    entity_type: str
    entity_id: int | None
    summary: str


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


@dataclass(frozen=True, slots=True)
class FinancialSummary:
    expense_cents: int
    income_cents: int
    profit_cents: int
    inventory_cents: int

    @property
    def cash_flow_cents(self) -> int:
        return self.income_cents - self.expense_cents
