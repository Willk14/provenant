"""Money primitives. Floats never touch a financial amount."""
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")
ZERO = Decimal("0")


def to_decimal(value: Decimal | int | str) -> Decimal:
    """Parse an amount. Floats are rejected because they cannot represent cents exactly."""
    if isinstance(value, (float, bool)):
        raise TypeError(f"use Decimal or str for money, not {type(value).__name__}")
    try:
        result = Decimal(str(value).replace(",", "").strip())
    except InvalidOperation as exc:
        raise ValueError(f"not a valid amount: {value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"not a finite amount: {value!r}")
    return result


def round_money(value: Decimal) -> Decimal:
    """Round to cents, half-up (the convention auditors expect in schedules)."""
    return value.quantize(CENT, rounding=ROUND_HALF_UP)
