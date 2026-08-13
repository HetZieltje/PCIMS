"""Immutable records returned by the PCIMS data layer."""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class Expense:
    id: int
    name: str
    item_type: str
    price_cents: int
    purchase_date: date
    pc_id: int | None = None
    pc_name: str | None = None
    sale_id: int | None = None

    @property
    def is_available(self):
        return self.pc_id is None and self.sale_id is None


@dataclass(frozen=True, slots=True)
class AssembledPC:
    id: int
    name: str
    parts: tuple[Expense, ...]

    @property
    def cost_cents(self):
        return sum(part.price_cents for part in self.parts)


@dataclass(frozen=True, slots=True)
class Sale:
    id: int
    name: str
    kind: str
    cost_cents: int
    selling_price_cents: int
    sale_date: date
    items: tuple[Expense, ...]

    @property
    def profit_cents(self):
        return self.selling_price_cents - self.cost_cents


@dataclass(frozen=True, slots=True)
class FinancialSummary:
    expense_cents: int
    income_cents: int
    profit_cents: int
    inventory_cents: int

    @property
    def cash_flow_cents(self):
        return self.income_cents - self.expense_cents
