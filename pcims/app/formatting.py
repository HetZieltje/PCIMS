"""Presentation-level money formatting and allocation."""

from decimal import Decimal

from pcims.money import parse_money_cents

__all__ = (
    "allocate_cents",
    "format_cents",
    "format_percentage_basis_points",
    "parse_money_cents",
)


def format_cents(cents: int) -> str:
    cents = int(cents)
    sign = "-" if cents < 0 else ""
    return f"{sign}€{Decimal(abs(cents)) / 100:,.2f}"


def format_percentage_basis_points(basis_points: int | None) -> str:
    """Format hundredths of a percentage point without binary-float rounding."""
    if basis_points is None:
        return "N/A"
    return f"{Decimal(basis_points) / 100:,.2f}%"


def allocate_cents(total_cents: int, count: int) -> list[int]:
    if count < 1:
        raise ValueError("Quantity must be at least one.")
    base, remainder = divmod(int(total_cents), count)
    return [base + (1 if index < remainder else 0) for index in range(count)]
