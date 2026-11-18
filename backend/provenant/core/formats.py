"""What a verified term must look like, for every kind of document.

Applied to human input (manual entry, corrections) and again before any calculation. The shapes are
deliberately strict: a rate must carry "%" ("12" vs "0.12" is the ambiguity that ends up 100x wrong), and a
covenant ratio must carry "x" ("3.5" could be a ratio, a percentage or an amount).
"""
import re
from datetime import date
from decimal import Decimal

_DATE = (r"\d{4}-\d{2}-\d{2}", "a date as YYYY-MM-DD")
_AMOUNT = (r"\d{1,3}(,\d{3})*(\.\d{1,2})?|\d+(\.\d{1,2})?", "an amount like 120,000 or 120000.00")
_RATIO = (r"\d{1,2}(\.\d{1,2})?x", "a ratio like 3.50x")
_DAYS = (r"\d{1,3}", "a whole number of days")

FORMATS: dict[str, tuple[str, str]] = {
    # leases and amendments
    "commencement_date": _DATE,
    "effective_date": _DATE,  # an amendment's IFRS 16 effective date: when it is agreed (usually signed)
    "terms_start_date": _DATE,  # when an amendment's new rent or extended term starts
    "expiry_date": _DATE,  # an amendment's new expiry date
    # Added space under IFRS 16.44: a preparer's judgement, entered with its basis (never extracted).
    "expansion_treatment": (r"(?i)separate lease|modification", "separate lease or modification"),
    "term_months": (r"\d{1,3}", "a whole number of months"),
    "monthly_payment": _AMOUNT,
    "annual_discount_rate": (r"\d{1,2}(\.\d+)?%", "a percentage like 12% or 8.5%"),
    "payment_timing": (r"(?i)advance|arrears", "advance or arrears"),
    # Rent schedules: steps by month number ("13: 1,030.00", for leases that say "Month 13") or by date
    # ("2026-06-01: 1,060.90", for rent tables), and rent-free months by number or YYYY-MM. The core turns
    # dates into periods; nobody has to count months by hand.
    "rent_steps": (r"[^;]+(;[^;]+)*", "steps like '13: 1,030.00; 25: 1,060.90' or '2026-06-01: 1,030.00'"),
    "free_periods": (r"[^,]+(,[^,]+)*", "months like '1, 2' or '2025-04, 2025-05'"),
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
    if name == "rent_steps":
        parse_rent_steps(value)
    if name == "free_periods":
        parse_free_periods(value)
    return value


_AMOUNT_RE = re.compile(FORMATS["monthly_payment"][0])


def parse_rent_steps(text: str) -> list[tuple[int | date, Decimal]]:
    """'13: 1,030.00; 2026-06-01: 1,060.90' -> [(13, Decimal('1030.00')), (date(2026, 6, 1), ...)]."""
    from provenant.core.money import to_decimal

    steps = []
    for part in text.split(";"):
        when, sep, amount = part.partition(":")
        when, amount = when.strip(), amount.strip()
        if not sep or not _AMOUNT_RE.fullmatch(amount):
            raise ValueError(f"rent step {part.strip()!r} must look like '13: 1,030.00' or '2026-06-01: 1,030.00'")
        if re.fullmatch(r"\d{1,3}", when):
            key: int | date = int(when)
        elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", when):
            key = date.fromisoformat(when)
        else:
            raise ValueError(f"rent step must start with a month number or YYYY-MM-DD, got {when!r}")
        steps.append((key, to_decimal(amount)))
    keys = [k for k, _ in steps]
    if len(set(keys)) != len(keys):
        raise ValueError("each rent step must start on a different month")
    return steps


def parse_free_periods(text: str) -> list[int | tuple[int, int]]:
    """'1, 2' -> [1, 2]; '2025-04, 2025-05' -> [(2025, 4), (2025, 5)]."""
    out: list[int | tuple[int, int]] = []
    for part in (p.strip() for p in text.split(",")):
        if re.fullmatch(r"\d{1,3}", part):
            out.append(int(part))
        elif re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", part):
            out.append((int(part[:4]), int(part[5:])))
        else:
            raise ValueError(f"rent-free month must be a month number or YYYY-MM, got {part!r}")
    if len(set(out)) != len(out):
        raise ValueError("a rent-free month is listed twice")
    return out
