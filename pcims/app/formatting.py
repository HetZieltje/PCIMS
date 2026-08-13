"""Presentation-level money formatting and allocation."""

from decimal import Decimal

from pcims.money import parse_money_cents

__all__ = (
    "allocate_cents",
    "cents_as_decimal",
    "format_cents",
    "parse_money_cents",
)


def cents_as_decimal(cents: int) -> Decimal:
    return Decimal(int(cents)) / 100


def format_cents(cents: int) -> str:
    cents = int(cents)
    sign = "-" if cents < 0 else ""
    return f"{sign}€{Decimal(abs(cents)) / 100:,.2f}"


def allocate_cents(total_cents: int, count: int) -> list[int]:
    if count < 1:
        raise ValueError("Quantity must be at least one.")
    base, remainder = divmod(int(total_cents), count)
    return [base + (1 if index < remainder else 0) for index in range(count)]
