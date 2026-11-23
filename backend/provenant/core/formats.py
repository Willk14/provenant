"""What a verified term must look like, for every kind of document.

Applied to human input (manual entry, corrections) and again before any calculation. The shapes are
deliberately strict: a rate must carry "%" ("12" vs "0.12" is the ambiguity that ends up 100x wrong), and a
covenant ratio must carry "x" ("3.5" could be a ratio, a percentage or an amount).
"""
import calendar
import re
from datetime import date
from decimal import Decimal

_DATE = (r"\d{4}-\d{2}-\d{2}", "a date as YYYY-MM-DD")
_AMOUNT = (r"\d{1,3}(,\d{3})*(\.\d{1,2})?|\d+(\.\d{1,2})?", "an amount like 120,000 or 120000.00")
_RATIO = (r"\d{1,2}(\.\d{1,2})?x", "a ratio like 3.50x")
_DAYS = (r"\d{1,3}", "a whole number of days")
_MONTHS = "|".join(calendar.month_name[1:])
_RELATIVE_TEST = rf"(?i:first (full )?quarter after (\d{{4}}-\d{{2}}-\d{{2}}), year end ({_MONTHS}))"
_RATIO_STEPS = (r"[^;]+(;[^;]+)*", "steps like '2027-03-31: 3.75x; 2027-12-31: 3.50x'")
_AMOUNT_STEPS = (r"[^;]+(;[^;]+)*", "steps like '2027-03-31: 60,000,000; 2027-12-31: 50,000,000'")

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
    # A named quarter-end, or the agreement's relative rule with the date and financial year-end it depends on.
    "first_test_date": (rf"\d{{4}}-\d{{2}}-\d{{2}}|{_RELATIVE_TEST}",
                        "a date as YYYY-MM-DD, or a rule like 'first full quarter after 2026-02-15, year end December'"),
    # Thresholds that change: each applies from that test date until the next step.
    "max_leverage_steps": _RATIO_STEPS,
    "min_interest_cover_steps": _RATIO_STEPS,
    "min_liquidity_steps": _AMOUNT_STEPS,
    "reporting_days": _DAYS,
    "cure_period_days": _DAYS,
    "cure_day_basis": (r"(?i)calendar|business", "calendar or business"),
    "cure_runs_from": (r"(?i)due date|delivery", "due date or delivery"),
    # A springing covenant: leverage is tested only when revolver usage exceeds this share of the commitment.
    "springing_usage_percent": (r"\d{1,3}(\.\d+)?%", "a percentage like 35%"),
    "revolver_commitment": _AMOUNT,
    # How the agreement defines covenant EBITDA and treats leases.
    "addback_cap_percent": (r"\d{1,2}(\.\d+)?%", "a percentage below 100, like 20%"),
    "addback_cap_basis": (r"(?i)before add-backs|after add-backs", "before add-backs or after add-backs"),
    "lease_treatment": (r"(?i)frozen GAAP|IFRS 16", "frozen GAAP or IFRS 16"),
}


def check_value(name: str, value: str) -> str:
    """The value, stripped, if it has the right shape for `name`; otherwise ValueError saying what is expected."""
    if name not in FORMATS:
        raise ValueError(f"unknown term {name!r}")
    pattern, expected = FORMATS[name]
    value = value.strip()
    if not re.fullmatch(pattern, value):
        raise ValueError(f"{name.replace('_', ' ')} must be {expected}, got {value!r}")
    if name == "first_test_date":
        rule = parse_relative_test(value)
        date.fromisoformat(value if rule is None else rule[1].isoformat())
    elif name.endswith("_date"):
        date.fromisoformat(value)  # also rejects 2026-02-30
    if name in ("max_leverage_steps", "min_interest_cover_steps"):
        parse_threshold_steps(value, ratio=True)
    if name == "min_liquidity_steps":
        parse_threshold_steps(value, ratio=False)
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


def parse_relative_test(text: str) -> tuple[bool, date, int] | None:
    """'first full quarter after 2026-02-15, year end December' -> (True, date(2026, 2, 15), 12); None for a date."""
    match = re.fullmatch(_RELATIVE_TEST, text.strip())
    if match is None:
        return None
    months = [m.lower() for m in calendar.month_name]
    return bool(match.group(1)), date.fromisoformat(match.group(2)), months.index(match.group(3).lower())


def parse_threshold_steps(text: str, *, ratio: bool) -> list[tuple[date, Decimal]]:
    """'2027-03-31: 3.75x; 2027-12-31: 3.50x' -> dated thresholds, oldest first."""
    from provenant.core.money import to_decimal

    shape = FORMATS["max_leverage_ratio" if ratio else "min_liquidity"][0]
    steps = []
    for part in text.split(";"):
        when, sep, amount = (x.strip() for x in part.partition(":"))
        if not sep or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", when) or not re.fullmatch(shape, amount):
            example = "3.75x" if ratio else "60,000,000"
            raise ValueError(f"threshold step {part.strip()!r} must look like '2027-03-31: {example}'")
        steps.append((date.fromisoformat(when), to_decimal(amount.rstrip("x"))))
    dates = [d for d, _ in steps]
    if len(set(dates)) != len(dates):
        raise ValueError("each threshold step must start on a different test date")
    return sorted(steps)


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
