"""Presentation-level money parsing and formatting."""

from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal, InvalidOperation

from db.queries import MAX_MONEY_CENTS


def parse_money_cents(value):
    normalized = str(value).strip().replace("€", "").replace(" ", "").replace(",", ".")
    try:
        amount = Decimal(normalized)
    except InvalidOperation as exc:
        raise ValueError("Enter a valid monetary amount.") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError("Amount must be finite and non-negative.")
    if amount.as_tuple().exponent < -2:
        raise ValueError("Amount can have at most two decimal places.")
    cents = int((amount * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    if cents > MAX_MONEY_CENTS:
        raise ValueError("Amount is too large.")
    return cents


def cents_as_decimal(cents):
    return Decimal(int(cents)) / 100


def format_cents(cents):
    return f"€{Decimal(int(cents)) / 100:,.2f}"


def allocate_cents(total_cents, count):
    if count < 1:
        raise ValueError("Quantity must be at least one.")
    base, remainder = divmod(int(total_cents), count)
    return [base + (1 if index < remainder else 0) for index in range(count)]


def allocate_weighted_cents(total_cents, weights):
    """Useful for imports and future allocation UIs; preserves every cent."""
    weights = [Decimal(str(weight)) for weight in weights]
    if not weights or any(weight < 0 for weight in weights) or sum(weights) <= 0:
        raise ValueError("Allocation weights must contain a positive value.")
    raw = [Decimal(total_cents) * weight / sum(weights) for weight in weights]
    result = [int(value.to_integral_value(rounding=ROUND_FLOOR)) for value in raw]
    for index in sorted(
        range(len(raw)), key=lambda item: raw[item] - result[item], reverse=True
    )[: int(total_cents) - sum(result)]:
        result[index] += 1
    return result
