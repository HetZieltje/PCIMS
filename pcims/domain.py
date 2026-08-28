"""Validated, immutable domain values and closed-set vocabulary for PCIMS."""

from collections.abc import Iterable
from dataclasses import dataclass, field
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
SaleKind: TypeAlias = Literal["item", "pc", "laptop"]
LaptopComponentType: TypeAlias = Literal["RAM", "SSD", "HDD"]
ItemCondition: TypeAlias = Literal["New", "Used", "Refurbished", "For parts"]
MAX_NAME_LENGTH = 200
MAX_NOTES_LENGTH = 4_000

ITEM_CONDITIONS: tuple[ItemCondition, ...] = (
    "New",
    "Used",
    "Refurbished",
    "For parts",
)

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

LAPTOP_COMPONENT_TYPES: tuple[LaptopComponentType, ...] = ("RAM", "SSD", "HDD")


def normalized_text(value: object, label: str) -> str:
    normalized = str(value).strip() if value is not None else ""
    if not normalized:
        raise ValueError(f"{label} cannot be blank.")
    if len(normalized) > MAX_NAME_LENGTH:
        raise ValueError(f"{label} cannot exceed {MAX_NAME_LENGTH} characters.")
    if not normalized.isprintable():
        raise ValueError(f"{label} must contain only printable characters.")
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


def normalized_optional_text(value: object, label: str) -> str:
    normalized = str(value).strip() if value is not None else ""
    if not normalized:
        return ""
    return normalized_text(normalized, label)


def normalized_notes(value: object) -> str:
    notes = str(value).strip() if value is not None else ""
    if len(notes) > MAX_NOTES_LENGTH:
        raise ValueError(f"Notes cannot exceed {MAX_NOTES_LENGTH} characters.")
    if any(
        not character.isprintable() and character not in "\n\t" for character in notes
    ):
        raise ValueError("Notes contain unsupported control characters.")
    return notes


@dataclass(frozen=True, slots=True)
class ItemDetails:
    vendor: str = ""
    serial_number: str = ""
    storage_location: str = ""
    condition: ItemCondition | None = None
    warranty_until: date | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "vendor", normalized_optional_text(self.vendor, "Vendor")
        )
        object.__setattr__(
            self,
            "serial_number",
            normalized_optional_text(self.serial_number, "Serial number"),
        )
        object.__setattr__(
            self,
            "storage_location",
            normalized_optional_text(self.storage_location, "Storage location"),
        )
        if self.condition is not None and self.condition not in ITEM_CONDITIONS:
            raise ValueError(f"Condition must be one of: {', '.join(ITEM_CONDITIONS)}.")
        if self.warranty_until is not None and type(self.warranty_until) is not date:
            raise TypeError("Warranty date must be a date or omitted.")
        object.__setattr__(self, "notes", normalized_notes(self.notes))

    @property
    def is_empty(self) -> bool:
        return not any(
            (
                self.vendor,
                self.serial_number,
                self.storage_location,
                self.condition,
                self.warranty_until,
                self.notes,
            )
        )


@dataclass(frozen=True, slots=True)
class NewExpense:
    name: str
    item_type: ItemType
    price_cents: int
    purchase_date: date
    details: ItemDetails = field(default_factory=ItemDetails)

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", normalized_text(self.name, "Item name"))
        object.__setattr__(self, "item_type", normalized_item_type(self.item_type))
        if (
            isinstance(self.price_cents, bool)
            or not isinstance(self.price_cents, int)
            or not 0 <= self.price_cents <= MAX_MONEY_CENTS
        ):
            raise ValueError("Price must be integer cents within the supported range.")
        if type(self.purchase_date) is not date:
            raise TypeError("Purchase date must be a date.")
        if not isinstance(self.details, ItemDetails):
            raise TypeError("Item details must be validated item details.")

    @classmethod
    def create(
        cls,
        name: object,
        item_type: object,
        price: object,
        purchase_date: object | None = None,
        details: ItemDetails | None = None,
    ) -> "NewExpense":
        return cls(
            normalized_text(name, "Item name"),
            normalized_item_type(item_type),
            parse_money_cents(price, "Price"),
            normalized_date(purchase_date),
            details or ItemDetails(),
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
        if type(self.sale_date) is not date:
            raise TypeError("Sale date must be a date.")

    @classmethod
    def create(
        cls, selling_price: object, sale_date: object | None = None
    ) -> "SaleTerms":
        return cls(
            parse_money_cents(selling_price, "Selling price"),
            normalized_date(sale_date),
        )


def normalized_laptop_component_type(value: object) -> LaptopComponentType:
    normalized = normalized_text(value, "Laptop component type").casefold()
    for item_type in LAPTOP_COMPONENT_TYPES:
        if item_type.casefold() == normalized:
            return item_type
    raise ValueError(
        "Laptop component type must be one of: "
        + ", ".join(LAPTOP_COMPONENT_TYPES)
        + "."
    )


@dataclass(frozen=True, slots=True)
class LaptopSlotRef:
    """A validated laptop RAM/storage slot identity."""

    component_type: LaptopComponentType
    slot_number: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "component_type",
            normalized_laptop_component_type(self.component_type),
        )
        object.__setattr__(
            self, "slot_number", normalized_id(self.slot_number, "Slot number")
        )

    @classmethod
    def create(cls, component_type: object, slot_number: object) -> "LaptopSlotRef":
        return cls(
            normalized_laptop_component_type(component_type),
            normalized_id(slot_number, "Slot number"),
        )
