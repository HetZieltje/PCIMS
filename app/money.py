"""Exact cent-level helpers for purchase entry workflows."""

from decimal import Decimal, InvalidOperation, ROUND_FLOOR, ROUND_HALF_UP


def decimal_value(value, label="Amount"):
    try:
        amount = Decimal(str(value).strip().replace("€", "").replace(",", "."))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric.") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError(f"{label} must be a finite, non-negative number.")
    return amount


def money_cents(value, label="Amount"):
    return int((decimal_value(value, label) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def allocate_total(value, weights):
    """Allocate a total by weight without losing a cent."""
    weights = [decimal_value(weight, "Allocation percentage") for weight in weights]
    if not weights or sum(weights) <= 0:
        raise ValueError("At least one positive allocation is required.")
    total_cents = money_cents(value)
    weight_total = sum(weights)
    raw = [Decimal(total_cents) * weight / weight_total for weight in weights]
    allocated = [int(amount.to_integral_value(rounding=ROUND_FLOOR)) for amount in raw]
    remainder = total_cents - sum(allocated)
    order = sorted(range(len(raw)), key=lambda index: raw[index] - allocated[index], reverse=True)
    for index in order[:remainder]:
        allocated[index] += 1
    return [Decimal(cents) / 100 for cents in allocated]
