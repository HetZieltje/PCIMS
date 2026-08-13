"""Shared closed-set domain vocabulary for PCIMS."""

from typing import Literal, TypeAlias

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
