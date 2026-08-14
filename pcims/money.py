"""Exact, dependency-free money parsing shared by the UI and data layer."""

import re

MAX_MONEY_CENTS = 99_999_999_999

_UNGROUPED = re.compile(r"^\d+(?:[.,]\d{1,2})?$")
_GROUPED_US = re.compile(r"^\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?$")
_GROUPED_EU = re.compile(r"^\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?$")


def _normalize_number(value: object) -> str | None:
    text = "".join(str(value).replace("€", "").split())
    if _GROUPED_US.fullmatch(text):
        return text.replace(",", "")
    if _GROUPED_EU.fullmatch(text):
        return text.replace(".", "").replace(",", ".")
    if _UNGROUPED.fullmatch(text):
        return text.replace(",", ".")
    return None


def parse_money_cents(value: object, label: str = "Amount") -> int:
    """Parse plain, US-grouped, or EU-grouped money without floating point."""
    normalized = _normalize_number(value)
    if normalized is None:
        raise ValueError(f"{label} must be a valid monetary amount.")
    whole, separator, fraction = normalized.partition(".")
    significant_whole = whole.lstrip("0") or "0"
    max_whole_digits = len(str(MAX_MONEY_CENTS // 100))
    if len(significant_whole) > max_whole_digits:
        raise ValueError(f"{label} is too large.")
    cents = int(significant_whole) * 100
    if separator:
        cents += int(fraction.ljust(2, "0"))
    if cents > MAX_MONEY_CENTS:
        raise ValueError(f"{label} is too large.")
    return cents
