"""Money primitives. Floats never touch a financial amount."""
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")
ZERO = Decimal("0")
_GROUPED = re.compile(r"-?\d{1,3}(,\d{3})+(\.\d+)?")


def to_decimal(value: Decimal | int | str) -> Decimal:
    """Parse an amount. Floats are rejected because they cannot represent cents exactly."""
    if isinstance(value, (float, bool)):
        raise TypeError(f"use Decimal or str for money, not {type(value).__name__}")
    text = str(value).strip()
    # Commas only as proper thousands separators: "120,000120,000" must fail, not become 120 billion.
    if "," in text and not _GROUPED.fullmatch(text):
        raise ValueError(f"misplaced thousands separator in {value!r}")
    try:
        result = Decimal(text.replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError(f"not a valid amount: {value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"not a finite amount: {value!r}")
    return result


def round_money(value: Decimal) -> Decimal:
    """Round to cents, half-up (the convention auditors expect in schedules)."""
    return value.quantize(CENT, rounding=ROUND_HALF_UP)
