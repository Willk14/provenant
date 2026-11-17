"""Financial covenant testing from trial balances. Deterministic: same terms and balances, same answer.

Conventions
- Trial balance lines carry a signed balance: debit positive, credit negative. Each line is mapped to a
  Category; totals are returned in the category's natural sign (revenue and liabilities as positives).
- Quarterly trial balances hold the quarter's P&L only. Last-twelve-months (LTM) P&L is the sum of four
  consecutive quarters; balance-sheet figures come from the test date's trial balance.
  shortcut: year-to-date P&L would need differencing between periods; add when a client sends YTD files.
- EBITDA = revenue - operating expenses, where depreciation, amortisation and interest are mapped to their
  own categories. shortcut: no agreement-specific add-backs or frozen-GAAP lease adjustments yet.
"""
import calendar
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum

from provenant.core.formats import check_value
from provenant.core.money import ZERO, to_decimal
from provenant.provenance import ExtractedField


class Category(StrEnum):
    CASH = "cash"
    BORROWINGS = "borrowings"
    LEASE_LIABILITIES = "lease_liabilities"
    REVENUE = "revenue"
    OPERATING_EXPENSES = "operating_expenses"
    DEPRECIATION_AMORTISATION = "depreciation_amortisation"
    INTEREST_EXPENSE = "interest_expense"
    OTHER = "other"


BALANCE_SHEET = frozenset({Category.CASH, Category.BORROWINGS, Category.LEASE_LIABILITIES})
PROFIT_AND_LOSS = frozenset(
    {Category.REVENUE, Category.OPERATING_EXPENSES, Category.DEPRECIATION_AMORTISATION, Category.INTEREST_EXPENSE}
)
_CREDIT_NATURE = frozenset({Category.BORROWINGS, Category.LEASE_LIABILITIES, Category.REVENUE})


@dataclass(frozen=True)
class TBLine:
    code: str
    name: str
    category: Category
    balance: Decimal  # debit positive, credit negative


@dataclass(frozen=True)
class TrialBalance:
    period_end: date
    lines: tuple[TBLine, ...]

    def __post_init__(self) -> None:
        difference = sum((line.balance for line in self.lines), ZERO)
        if difference != 0:
            raise ValueError(f"trial balance for {self.period_end} does not balance (off by {difference})")

    def total(self, category: Category) -> Decimal:
        amount = sum((line.balance for line in self.lines if line.category is category), ZERO)
        return -amount if category in _CREDIT_NATURE else amount


class Kind(StrEnum):
    LEVERAGE = "leverage"  # net debt / LTM EBITDA, at most the threshold
    INTEREST_COVER = "interest_cover"  # LTM EBITDA / LTM interest, at least the threshold
    MIN_LIQUIDITY = "min_liquidity"  # cash, at least the threshold


@dataclass(frozen=True)
class Covenant:
    kind: Kind
    threshold: Decimal


@dataclass(frozen=True)
class Figures:
    """What the tests read: balance-sheet figures at the test date and LTM P&L."""

    test_date: date
    totals: Mapping[Category, Decimal]
    periods: tuple[date, ...]  # the trial balances used, oldest first

    @property
    def ebitda(self) -> Decimal:
        return self.totals[Category.REVENUE] - self.totals[Category.OPERATING_EXPENSES]

    @property
    def net_debt(self) -> Decimal:
        t = self.totals
        return t[Category.BORROWINGS] + t[Category.LEASE_LIABILITIES] - t[Category.CASH]


@dataclass(frozen=True)
class Working:
    label: str
    amount: Decimal


@dataclass(frozen=True)
class Result:
    covenant: Covenant
    status: str  # pass | warning | breach
    value: Decimal | None  # the ratio or amount tested; None when it is not meaningful
    headroom: Decimal | None  # how far EBITDA (or cash) could fall before a breach, as a fraction
    workings: tuple[Working, ...]
    note: str = ""


@dataclass(frozen=True)
class TestDate:
    period_end: date
    certificate_due: date
    results: tuple[Result, ...]  # empty when the test could not run yet
    cure_deadline: date | None  # set when something is in breach and the agreement has a cure period
    missing: str | None = None  # why the test could not run (e.g. trial balances missing)


def month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def add_quarters(period_end: date, quarters: int) -> date:
    index = period_end.year * 12 + period_end.month - 1 + 3 * quarters
    return month_end(index // 12, index % 12 + 1)


def quarters_back(period_end: date, count: int) -> list[date]:
    """`count` quarter-ends ending at `period_end`, oldest first."""
    return [add_quarters(period_end, -i) for i in reversed(range(count))]


def figures(trial_balances: Mapping[date, TrialBalance], test_date: date) -> Figures:
    """Raises LookupError naming the trial balances that are missing."""
    periods = quarters_back(test_date, 4)
    missing = [d for d in periods if d not in trial_balances]
    if missing:
        raise LookupError(f"needs trial balances for {', '.join(d.isoformat() for d in missing)}")
    totals = {c: trial_balances[test_date].total(c) for c in BALANCE_SHEET}
    totals.update({c: sum((trial_balances[d].total(c) for d in periods), ZERO) for c in PROFIT_AND_LOSS})
    return Figures(test_date, totals, tuple(periods))


def evaluate(covenant: Covenant, f: Figures, warning_headroom: Decimal) -> Result:
    t, threshold = f.totals, covenant.threshold
    ebitda_lines = (
        Working("Revenue (LTM)", t[Category.REVENUE]),
        Working("Operating expenses (LTM)", -t[Category.OPERATING_EXPENSES]),
        Working("EBITDA (LTM)", f.ebitda),
    )
    note = ""
    if covenant.kind is Kind.LEVERAGE:
        workings = (
            Working("Borrowings", t[Category.BORROWINGS]),
            Working("Lease liabilities", t[Category.LEASE_LIABILITIES]),
            Working("Cash", -t[Category.CASH]),
            Working("Net debt", f.net_debt),
            *ebitda_lines,
        )
        if f.ebitda <= 0:
            value, passes, headroom = None, False, None
            note = "EBITDA is not positive, so leverage cannot meet any maximum"
        else:
            value = f.net_debt / f.ebitda
            passes, headroom = value <= threshold, 1 - value / threshold
    elif covenant.kind is Kind.INTEREST_COVER:
        workings = (*ebitda_lines, Working("Interest expense (LTM)", t[Category.INTEREST_EXPENSE]))
        if t[Category.INTEREST_EXPENSE] <= 0:
            value, passes, headroom = None, True, None
            note = "no interest expense in the period"
        else:
            value = f.ebitda / t[Category.INTEREST_EXPENSE]
            passes = value >= threshold
            headroom = 1 - threshold / value if value > 0 else None
    else:
        workings = (Working("Cash", t[Category.CASH]),)
        value = t[Category.CASH]
        passes = value >= threshold
        headroom = 1 - threshold / value if value > 0 else None

    # Compare the exact value; rounding is for display only, so it can never flip a result.
    if not passes:
        status = "breach"
    elif headroom is not None and headroom < warning_headroom:
        status = "warning"
    else:
        status = "pass"
    return Result(covenant, status, value, headroom, workings, note)


@dataclass(frozen=True)
class Terms:
    covenants: tuple[Covenant, ...]
    first_test_date: date
    reporting_days: int  # compliance certificate due this many days after each quarter-end
    cure_days: int | None = None  # equity cure window after the certificate is due
    warning_headroom: Decimal = Decimal("0.10")

    FIELDS = ("max_leverage_ratio", "min_interest_cover", "min_liquidity", "first_test_date", "reporting_days",
              "cure_period_days")
    REQUIRED = ("first_test_date", "reporting_days")
    COVENANT_FIELDS = {
        "max_leverage_ratio": Kind.LEVERAGE,
        "min_interest_cover": Kind.INTEREST_COVER,
        "min_liquidity": Kind.MIN_LIQUIDITY,
    }

    @classmethod
    def from_fields(cls, fields: Mapping[str, ExtractedField]) -> "Terms":
        """Every term present must be verified. Rejecting a proposed covenant records that the agreement has no
        such covenant; at least one must remain."""
        present = {n: f for n, f in fields.items() if n in cls.FIELDS and f.status != "rejected"}
        missing = [n for n in cls.REQUIRED if n not in present]
        if missing:
            raise ValueError(f"missing credit agreement fields: {', '.join(missing)}")
        unverified = [n for n, f in present.items() if not f.verified]
        if unverified:
            raise PermissionError(f"fields not verified: {', '.join(sorted(unverified))}")

        def value(name: str) -> str:
            return check_value(name, present[name].value)

        covenants = tuple(
            Covenant(kind, to_decimal(value(name).rstrip("x")))
            for name, kind in cls.COVENANT_FIELDS.items()
            if name in present
        )
        return cls(
            covenants=covenants,
            first_test_date=date.fromisoformat(value("first_test_date")),
            reporting_days=int(value("reporting_days")),
            cure_days=int(value("cure_period_days")) if "cure_period_days" in present else None,
        )

    def field_for(self, kind: Kind) -> str:
        return next(name for name, k in self.COVENANT_FIELDS.items() if k is kind)

    def __post_init__(self) -> None:
        if not self.covenants:
            raise ValueError("a credit agreement needs at least one financial covenant")
        if self.first_test_date != month_end(self.first_test_date.year, self.first_test_date.month):
            raise ValueError("the first test date must be a month-end")
        if not 0 < self.reporting_days <= 365:
            raise ValueError("reporting days must be between 1 and 365")


def run(terms: Terms, trial_balances: Sequence[TrialBalance], horizon: date) -> list[TestDate]:
    """Every quarterly test date from the first test date up to `horizon`: tested where the trial balances
    allow, otherwise listed with its deadline and what is missing."""
    by_date = {tb.period_end: tb for tb in trial_balances}
    if len(by_date) != len(trial_balances):
        raise ValueError("two trial balances for the same period")
    out: list[TestDate] = []
    test_date = terms.first_test_date
    while test_date <= horizon:
        due = test_date + timedelta(days=terms.reporting_days)
        try:
            f = figures(by_date, test_date)
        except LookupError as exc:
            out.append(TestDate(test_date, due, (), None, str(exc)))
        else:
            results = tuple(evaluate(c, f, terms.warning_headroom) for c in terms.covenants)
            breached = any(r.status == "breach" for r in results)
            cure = due + timedelta(days=terms.cure_days) if breached and terms.cure_days else None
            out.append(TestDate(test_date, due, results, cure))
        test_date = add_quarters(test_date, 1)
    return out
