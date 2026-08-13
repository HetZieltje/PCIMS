"""Shared closed-set domain vocabulary for PCIMS."""

from typing import Literal, NotRequired, TypeAlias, TypedDict

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


class PurchaseInput(TypedDict):
    name: object
    item_type: object
    price: object
    purchase_date: NotRequired[object]

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
