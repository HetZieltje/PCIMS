"""Exact, dependency-free money parsing shared by the UI and data layer."""

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

MAX_MONEY_CENTS = 99_999_999_999

_UNGROUPED = re.compile(r"^\d+(?:[.,]\d{1,2})?$")
_GROUPED_US = re.compile(r"^\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?$")
_GROUPED_EU = re.compile(r"^\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?$")


def _normalize_number(value):
    text = "".join(str(value).replace("€", "").split())
    if _GROUPED_US.fullmatch(text):
        return text.replace(",", "")
    if _GROUPED_EU.fullmatch(text):
        return text.replace(".", "").replace(",", ".")
    if _UNGROUPED.fullmatch(text):
        return text.replace(",", ".")
    return None


def parse_money_cents(value, label="Amount"):
    """Parse plain, US-grouped, or EU-grouped money without floating point."""
    normalized = _normalize_number(value)
    if normalized is None:
        raise ValueError(f"{label} must be a valid monetary amount.")
    try:
        amount = Decimal(normalized)
    except InvalidOperation as exc:
        raise ValueError(f"{label} must be a valid monetary amount.") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError(f"{label} must be finite and non-negative.")
    cents = int((amount * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    if cents > MAX_MONEY_CENTS:
        raise ValueError(f"{label} is too large.")
    return cents
