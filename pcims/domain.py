"""Validated, immutable domain values and closed-set vocabulary for PCIMS."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal, TypeAlias

from pcims.money import MAX_MONEY_CENTS, parse_money_cents

ItemType: TypeAlias = Literal[
    "CPU",
    "Cooler",
    "GPU",
    "Motherboard",
    "RAM",
    "SSD",
    "HDD",
    "Case",
    "PSU",
    "Fan",
    "Extra",
]
SaleKind: TypeAlias = Literal["item", "pc"]

ITEM_TYPES: tuple[ItemType, ...] = (
    "CPU",
    "Cooler",
    "GPU",
    "Motherboard",
    "RAM",
    "SSD",
    "HDD",
    "Case",
    "PSU",
    "Fan",
    "Extra",
)


def normalized_text(value: object, label: str) -> str:
    normalized = str(value).strip() if value is not None else ""
    if not normalized:
        raise ValueError(f"{label} cannot be blank.")
    return normalized


def normalized_item_type(value: object) -> ItemType:
    normalized = normalized_text(value, "Item type").casefold()
    for item_type in ITEM_TYPES:
        if item_type.casefold() == normalized:
            return item_type
    raise ValueError(f"Item type must be one of: {', '.join(ITEM_TYPES)}.")


def normalized_id(value: object, label: str = "ID") -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be a positive integer.") from error
    if parsed <= 0:
        raise ValueError(f"{label} must be a positive integer.")
    return parsed


def normalized_ids(values: Iterable[object], label: str) -> tuple[int, ...]:
    identifiers = tuple(normalized_id(value, label) for value in values)
    if not identifiers:
        raise ValueError(f"At least one {label.lower()} is required.")
    if len(identifiers) != len(set(identifiers)):
        raise ValueError(f"Duplicate {label.lower()} values are not allowed.")
    return identifiers


def normalized_date(value: object | None) -> date:
    if value is None:
        return datetime.now(UTC).astimezone().date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except (TypeError, ValueError) as error:
        raise ValueError("Date must use the YYYY-MM-DD format.") from error


@dataclass(frozen=True, slots=True)
class NewExpense:
    name: str
    item_type: ItemType
    price_cents: int
    purchase_date: date

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", normalized_text(self.name, "Item name"))
        object.__setattr__(self, "item_type", normalized_item_type(self.item_type))
        if (
            isinstance(self.price_cents, bool)
            or not isinstance(self.price_cents, int)
            or not 0 <= self.price_cents <= MAX_MONEY_CENTS
        ):
            raise ValueError("Price must be integer cents within the supported range.")
        if not isinstance(self.purchase_date, date):
            raise TypeError("Purchase date must be a date.")

    @classmethod
    def create(
        cls,
        name: object,
        item_type: object,
        price: object,
        purchase_date: object | None = None,
    ) -> "NewExpense":
        return cls(
            normalized_text(name, "Item name"),
            normalized_item_type(item_type),
            parse_money_cents(price, "Price"),
            normalized_date(purchase_date),
        )


@dataclass(frozen=True, slots=True)
class SaleTerms:
    selling_price_cents: int
    sale_date: date

    def __post_init__(self) -> None:
        if (
            isinstance(self.selling_price_cents, bool)
            or not isinstance(self.selling_price_cents, int)
            or not 0 <= self.selling_price_cents <= MAX_MONEY_CENTS
        ):
            raise ValueError(
                "Selling price must be integer cents within the supported range."
            )
        if not isinstance(self.sale_date, date):
            raise TypeError("Sale date must be a date.")

    @classmethod
    def create(
        cls, selling_price: object, sale_date: object | None = None
    ) -> "SaleTerms":
        return cls(
            parse_money_cents(selling_price, "Selling price"),
            normalized_date(sale_date),
        )
