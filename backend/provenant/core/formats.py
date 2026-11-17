"""What a verified term must look like, for every kind of document.

Applied to human input (manual entry, corrections) and again before any calculation. The shapes are
deliberately strict: a rate must carry "%" ("12" vs "0.12" is the ambiguity that ends up 100x wrong), and a
covenant ratio must carry "x" ("3.5" could be a ratio, a percentage or an amount).
"""
import re
from datetime import date

_DATE = (r"\d{4}-\d{2}-\d{2}", "a date as YYYY-MM-DD")
_AMOUNT = (r"\d{1,3}(,\d{3})*(\.\d{1,2})?|\d+(\.\d{1,2})?", "an amount like 120,000 or 120000.00")
_RATIO = (r"\d{1,2}(\.\d{1,2})?x", "a ratio like 3.50x")
_DAYS = (r"\d{1,3}", "a whole number of days")

FORMATS: dict[str, tuple[str, str]] = {
    # leases and amendments
    "commencement_date": _DATE,
    "effective_date": _DATE,
    "term_months": (r"\d{1,3}", "a whole number of months"),
    "monthly_payment": _AMOUNT,
    "annual_discount_rate": (r"\d{1,2}(\.\d+)?%", "a percentage like 12% or 8.5%"),
    "payment_timing": (r"(?i)advance|arrears", "advance or arrears"),
    # credit agreements
    "max_leverage_ratio": _RATIO,
    "min_interest_cover": _RATIO,
    "min_liquidity": _AMOUNT,
    "first_test_date": _DATE,
    "reporting_days": _DAYS,
    "cure_period_days": _DAYS,
}


def check_value(name: str, value: str) -> str:
    """The value, stripped, if it has the right shape for `name`; otherwise ValueError saying what is expected."""
    if name not in FORMATS:
        raise ValueError(f"unknown term {name!r}")
    pattern, expected = FORMATS[name]
    value = value.strip()
    if not re.fullmatch(pattern, value):
        raise ValueError(f"{name.replace('_', ' ')} must be {expected}, got {value!r}")
    if name.endswith("_date"):
        date.fromisoformat(value)  # also rejects 2026-02-30
    return value
