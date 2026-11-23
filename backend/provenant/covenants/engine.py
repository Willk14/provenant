"""Financial covenant testing from trial balances. Deterministic: same terms and balances, same answer.

Conventions
- Trial balance lines carry a signed balance: debit positive, credit negative. Each line is mapped to a
  Category; totals are returned in the category's natural sign (revenue and liabilities as positives).
- Quarterly trial balances hold the quarter's P&L only. Last-twelve-months (LTM) P&L is the sum of four
  consecutive quarters; balance-sheet figures come from the test date's trial balance.
  shortcut: year-to-date P&L would need differencing between periods; add when a client sends YTD files.
- EBITDA = revenue - operating expenses, where depreciation, amortisation and interest are mapped to their
  own categories. Agreement-defined add-backs, caps and frozen-GAAP leases are applied by `adjust`.
"""
import calendar
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum

from provenant.core.formats import check_value, parse_relative_test, parse_threshold_steps
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
    ebitda_lines: tuple["Working", ...] = ()  # agreement adjustments, each naming its source
    debt_lines: tuple["Working", ...] = ()
    interest_lines: tuple["Working", ...] = ()

    @property
    def ebitda(self) -> Decimal:
        reported = self.totals[Category.REVENUE] - self.totals[Category.OPERATING_EXPENSES]
        return reported + sum((w.amount for w in self.ebitda_lines), ZERO)

    @property
    def net_debt(self) -> Decimal:
        t = self.totals
        return (t[Category.BORROWINGS] + t[Category.LEASE_LIABILITIES] - t[Category.CASH]
                + sum((w.amount for w in self.debt_lines), ZERO))

    @property
    def interest(self) -> Decimal:
        return self.totals[Category.INTEREST_EXPENSE] + sum((w.amount for w in self.interest_lines), ZERO)


@dataclass(frozen=True)
class Working:
    label: str
    amount: Decimal
    source: str | None = None  # the adjustment or lease document behind this line


@dataclass(frozen=True)
class Adjustment:
    """An approved item the agreement's EBITDA definition adds back (positive) or deducts (negative). It belongs to
    one quarter, so it counts in every twelve-month period that contains that quarter."""

    quarter: date
    label: str
    amount: Decimal
    capped: bool
    source: str


@dataclass(frozen=True)
class FrozenLease:
    """A lease treated as an operating lease under the agreement's frozen GAAP. `periods` holds each month's end
    date, payment, IFRS 16 interest and closing liability from its verified schedule."""

    name: str
    source: str
    periods: tuple[tuple[date, Decimal, Decimal, Decimal], ...]

    def straight_line(self, after: date, through: date) -> Decimal:
        """The operating lease charge: total payments spread evenly over the term, for months in the window."""
        months = sum(after < end <= through for end, *_ in self.periods)
        return sum((p[1] for p in self.periods), ZERO) * months / len(self.periods)

    def interest(self, after: date, through: date) -> Decimal:
        return sum((p[2] for p in self.periods if after < p[0] <= through), ZERO)

    def liability(self, on: date) -> Decimal:
        reached = [p for p in self.periods if p[0] <= on]
        if reached:
            return reached[-1][3]
        return ZERO


@dataclass(frozen=True)
class Result:
    covenant: Covenant
    status: str  # pass | warning | breach | not_tested (springing, not triggered) | undetermined (usage unknown)
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
    cure_pending: bool = False  # in breach, and the cure window runs from a delivery date not yet confirmed


@dataclass(frozen=True)
class PeriodInput:
    """Facts of a test date that no trial balance holds, confirmed by a second person before they are used."""

    revolver_drawn: Decimal | None = None
    delivered_on: date | None = None


def month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def add_quarters(period_end: date, quarters: int) -> date:
    index = period_end.year * 12 + period_end.month - 1 + 3 * quarters
    return month_end(index // 12, index % 12 + 1)


def quarters_back(period_end: date, count: int) -> list[date]:
    """`count` quarter-ends ending at `period_end`, oldest first."""
    return [add_quarters(period_end, -i) for i in reversed(range(count))]


def resolve_first_test(full_quarter: bool, effective: date, year_end_month: int) -> date:
    """The quarter-end a relative rule names, on the financial year's quarter cycle. A full quarter is one that
    starts on or after the effective date; otherwise the quarter need only end strictly after it."""
    index = effective.year * 12 + effective.month - 1 + (year_end_month - effective.month) % 3
    quarter_end = month_end(index // 12, index % 12 + 1)  # the quarter the effective date falls in
    quarter_start = add_quarters(quarter_end, -1) + timedelta(days=1)
    qualifies = quarter_start >= effective if full_quarter else quarter_end > effective
    return quarter_end if qualifies else add_quarters(quarter_end, 1)


def add_business_days(start: date, days: int) -> date:
    """The date `days` weekdays after `start`. Public holidays are not removed: they vary by agreement and
    jurisdiction, and leaving them in counts them as days used, so the date is never later than the true one."""
    current = start
    while days:
        current += timedelta(days=1)
        days -= current.weekday() < 5
    return current


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
        *f.ebitda_lines,
        Working("EBITDA (LTM)", f.ebitda),
    )
    note = ""
    if covenant.kind is Kind.LEVERAGE:
        workings = (
            Working("Borrowings", t[Category.BORROWINGS]),
            Working("Lease liabilities", t[Category.LEASE_LIABILITIES]),
            *f.debt_lines,
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
        workings = (*ebitda_lines, Working("Interest expense (LTM)", t[Category.INTEREST_EXPENSE]), *f.interest_lines)
        if f.interest_lines:
            workings = (*workings, Working("Covenant interest (LTM)", f.interest))
        if f.interest <= 0:
            value, passes, headroom = None, True, None
            note = "no interest expense in the period"
        else:
            value = f.ebitda / f.interest
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
    cure_business_days: bool = False  # count the cure window in business days rather than calendar days
    cure_from_delivery: bool = False  # the window runs from the certificate's delivery rather than its due date
    springing_usage: Decimal | None = None  # leverage is tested only when usage exceeds this fraction
    revolver_commitment: Decimal | None = None
    threshold_steps: tuple[tuple[Kind, date, Decimal], ...] = ()  # each applies from its test date to the next
    addback_cap: Decimal | None = None  # capped add-backs may not exceed this fraction of EBITDA
    cap_after_addbacks: bool = False  # the cap is measured on EBITDA after the capped add-backs, not before
    frozen_gaap: bool = False  # confirmed leases are treated as operating leases

    FIELDS = ("max_leverage_ratio", "min_interest_cover", "min_liquidity", "max_leverage_steps",
              "min_interest_cover_steps", "min_liquidity_steps", "first_test_date", "reporting_days",
              "cure_period_days", "cure_day_basis", "cure_runs_from", "springing_usage_percent",
              "revolver_commitment", "addback_cap_percent", "addback_cap_basis", "lease_treatment")
    REQUIRED = ("first_test_date", "reporting_days")
    COVENANT_FIELDS = {
        "max_leverage_ratio": Kind.LEVERAGE,
        "min_interest_cover": Kind.INTEREST_COVER,
        "min_liquidity": Kind.MIN_LIQUIDITY,
    }
    STEP_FIELDS = {
        "max_leverage_steps": Kind.LEVERAGE,
        "min_interest_cover_steps": Kind.INTEREST_COVER,
        "min_liquidity_steps": Kind.MIN_LIQUIDITY,
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
        if ("addback_cap_percent" in present) != ("addback_cap_basis" in present):
            raise ValueError("an add-back cap needs both its percentage and whether it is before or after add-backs")
        rule = parse_relative_test(value("first_test_date"))
        steps = tuple(
            (kind, on, threshold)
            for name, kind in cls.STEP_FIELDS.items()
            if name in present
            for on, threshold in parse_threshold_steps(value(name), ratio=kind is not Kind.MIN_LIQUIDITY)
        )
        return cls(
            covenants=covenants,
            first_test_date=(resolve_first_test(*rule) if rule else date.fromisoformat(value("first_test_date"))),
            threshold_steps=steps,
            addback_cap=(to_decimal(value("addback_cap_percent").rstrip("%")) / 100
                         if "addback_cap_percent" in present else None),
            cap_after_addbacks="addback_cap_basis" in present and value("addback_cap_basis").lower() == "after add-backs",
            frozen_gaap="lease_treatment" in present and value("lease_treatment").lower() == "frozen gaap",
            reporting_days=int(value("reporting_days")),
            cure_days=int(value("cure_period_days")) if "cure_period_days" in present else None,
            cure_business_days="cure_day_basis" in present and value("cure_day_basis").lower() == "business",
            cure_from_delivery="cure_runs_from" in present and value("cure_runs_from").lower() == "delivery",
            springing_usage=(to_decimal(value("springing_usage_percent").rstrip("%")) / 100
                             if "springing_usage_percent" in present else None),
            revolver_commitment=(to_decimal(value("revolver_commitment"))
                                 if "revolver_commitment" in present else None),
        )

    def field_for(self, kind: Kind) -> str:
        return next(name for name, k in self.COVENANT_FIELDS.items() if k is kind)

    def step_at(self, kind: Kind, test_date: date) -> tuple[date, Decimal] | None:
        """The latest threshold step in force at `test_date`; None while the base threshold applies."""
        applying = [(on, threshold) for k, on, threshold in self.threshold_steps if k is kind and on <= test_date]
        return max(applying, default=None)

    def field_at(self, kind: Kind, test_date: date) -> str:
        """The term that supplied the threshold tested at `test_date`, so a result traces to the right clause."""
        if self.step_at(kind, test_date) is None:
            return self.field_for(kind)
        return next(name for name, k in self.STEP_FIELDS.items() if k is kind)

    def __post_init__(self) -> None:
        if not self.covenants:
            raise ValueError("a credit agreement needs at least one financial covenant")
        if self.first_test_date != month_end(self.first_test_date.year, self.first_test_date.month):
            raise ValueError("the first test date must be a month-end")
        if not 0 < self.reporting_days <= 365:
            raise ValueError("reporting days must be between 1 and 365")
        if (self.cure_business_days or self.cure_from_delivery) and self.cure_days is None:
            raise ValueError("the cure day basis and start need a cure period")
        if (self.springing_usage is None) != (self.revolver_commitment is None):
            raise ValueError("a springing covenant needs both the usage percentage and the revolver commitment")
        if self.springing_usage is not None:
            if not 0 < self.springing_usage <= 1:
                raise ValueError("the springing usage percentage must be above 0% and at most 100%")
            if self.revolver_commitment <= 0:
                raise ValueError("the revolver commitment must be more than zero")
            if not any(c.kind is Kind.LEVERAGE for c in self.covenants):
                raise ValueError("a springing test applies to the leverage covenant, and there is none")
        kinds = {c.kind for c in self.covenants}
        for kind, on, threshold in self.threshold_steps:
            label = kind.value.replace("_", " ")
            if kind not in kinds:
                raise ValueError(f"threshold steps for {label} need its base covenant")
            if threshold <= 0:
                raise ValueError(f"a {label} threshold step must be more than zero")
            if on <= self.first_test_date:
                raise ValueError(f"the {label} step on {on} must be after the first test date; "
                                 "the level at the first test date is the base threshold")
            if not self.on_test_cycle(on):
                raise ValueError(f"the {label} step on {on} is not a quarterly test date")
        if self.addback_cap is not None and not 0 < self.addback_cap < 1:
            raise ValueError("the add-back cap must be above 0% and below 100%")

    def on_test_cycle(self, on: date) -> bool:
        """Whether `on` is a quarter-end of the agreement's testing cycle, before or after the first test."""
        months = (on.year - self.first_test_date.year) * 12 + on.month - self.first_test_date.month
        return on == month_end(on.year, on.month) and months % 3 == 0

    def cure_deadline(self, due: date, delivered_on: date | None) -> date | None:
        """When the cure window closes; None if there is no cure right or its start is not known yet."""
        start = delivered_on if self.cure_from_delivery else due
        if not self.cure_days or start is None:
            return None
        if self.cure_business_days:
            return add_business_days(start, self.cure_days)
        return start + timedelta(days=self.cure_days)


def apply_covenant(covenant: Covenant, f: Figures, terms: Terms, drawn: Decimal | None) -> Result:
    """`evaluate`, unless the leverage covenant is springing: then only when usage exceeds the trigger. An unknown
    usage is never read as "not triggered"."""
    if covenant.kind is not Kind.LEVERAGE or terms.springing_usage is None:
        return evaluate(covenant, f, terms.warning_headroom)
    trigger = terms.springing_usage * terms.revolver_commitment
    percent = f"{(terms.springing_usage * 100).normalize():f}"
    if drawn is None:
        return Result(covenant, "undetermined", None, None,
                      (Working("Revolver commitment", terms.revolver_commitment), Working("Test trigger", trigger)),
                      f"Tested only above {percent}% usage: needs the confirmed revolver drawn at the test date")
    lines = (Working("Revolver drawn", drawn), Working("Revolver commitment", terms.revolver_commitment),
             Working("Test trigger", trigger))
    usage = f"{drawn / terms.revolver_commitment * 100:.2f}%"
    if drawn <= trigger:  # the test applies when usage exceeds the trigger, so exactly at it is not tested
        return Result(covenant, "not_tested", None, None, lines,
                      f"Not tested: revolver usage {usage} does not exceed {percent}%")
    result = evaluate(covenant, f, terms.warning_headroom)
    note = f"Tested: revolver usage {usage} exceeds {percent}%"
    return replace(result, workings=(*lines, *result.workings), note=f"{note}. {result.note}" if result.note else note)


def adjust(f: Figures, terms: Terms, adjustments: Sequence[Adjustment], leases: Sequence[FrozenLease],
           lease_problem: str | None = None) -> tuple[Figures, str | None]:
    """The figures under the agreement's definitions, and why they cannot be relied on, if they cannot.

    The cap is measured on EBITDA after frozen-GAAP rent and every uncapped item. Measured before add-backs, capped
    items may reach cap x base; measured after them, A <= cap x (base + A), so A may reach base x cap / (1 - cap)."""
    problem = lease_problem
    off_cycle = sorted({a.quarter for a in adjustments if not terms.on_test_cycle(a.quarter)})
    if off_cycle:
        problem = f"an approved adjustment is not on a quarterly test date: {off_cycle[0].isoformat()}"
    ebitda: list[Working] = []
    debt: list[Working] = []
    interest: list[Working] = []
    if terms.frozen_gaap:
        if not leases and problem is None:
            problem = "the agreement uses frozen GAAP: add and approve the leases it applies to"
        after = add_quarters(f.periods[0], -1)
        for lease in leases:
            ebitda.append(Working(f"Operating lease charge: {lease.name}", -lease.straight_line(after, f.test_date), lease.source))
            debt.append(Working(f"Lease liability removed: {lease.name}", -lease.liability(f.test_date), lease.source))
            interest.append(Working(f"Lease interest removed: {lease.name}", -lease.interest(after, f.test_date), lease.source))
        if -sum((w.amount for w in debt), ZERO) > f.totals[Category.LEASE_LIABILITIES] + Decimal("0.01"):
            problem = problem or "the lease schedules exceed the lease liabilities in the trial balance"
        if -sum((w.amount for w in interest), ZERO) > f.totals[Category.INTEREST_EXPENSE] + Decimal("0.01"):
            problem = problem or "the lease interest exceeds the interest expense in the trial balance"

    in_period = [a for a in adjustments if a.quarter in f.periods]

    def line(a: Adjustment) -> Working:
        word = "Add-back" if a.amount > 0 else "Deduction"
        return Working(f"{word}: {a.label} ({a.quarter.isoformat()})", a.amount, a.source)

    ebitda.extend(line(a) for a in in_period if not a.capped)
    reported = f.totals[Category.REVENUE] - f.totals[Category.OPERATING_EXPENSES]
    base = reported + sum((w.amount for w in ebitda), ZERO)  # before any capped item
    capped = [a for a in in_period if a.capped]
    ebitda.extend(line(a) for a in capped)
    if capped and terms.addback_cap is not None:
        share = terms.addback_cap / (1 - terms.addback_cap) if terms.cap_after_addbacks else terms.addback_cap
        allowed = max(base, ZERO) * share
        total = sum((a.amount for a in capped), ZERO)
        if total > allowed:
            ebitda.append(Working("Add-backs above the cap", allowed - total))
    return replace(f, ebitda_lines=tuple(ebitda), debt_lines=tuple(debt),
                   interest_lines=tuple(w for w in interest if w.amount)), problem


def apply_at_date(covenant: Covenant, f: Figures, terms: Terms, drawn: Decimal | None,
                  problem: str | None = None) -> Result:
    """`apply_covenant` at the threshold in force on the test date, saying so when a step set it. A ratio built on
    figures that cannot be relied on is undetermined, never a pass."""
    step = terms.step_at(covenant.kind, f.test_date)
    tested = covenant if step is None else replace(covenant, threshold=step[1])
    result = apply_covenant(tested, f, terms, drawn)
    if problem and covenant.kind is not Kind.MIN_LIQUIDITY and result.status != "not_tested":
        return replace(result, status="undetermined", value=None, headroom=None, workings=(), note=problem)
    if step is None:
        return result
    level = f"{step[1]:,.2f}" if covenant.kind is Kind.MIN_LIQUIDITY else f"{step[1]:.2f}x"
    note = f"Threshold {level} applies from {step[0].isoformat()}"
    return replace(result, note=f"{note}. {result.note}" if result.note else note)


def run(terms: Terms, trial_balances: Sequence[TrialBalance], horizon: date,
        period_inputs: Mapping[date, PeriodInput] | None = None, adjustments: Sequence[Adjustment] = (),
        leases: Sequence[FrozenLease] = (), lease_problem: str | None = None) -> list[TestDate]:
    """Every quarterly test date from the first test date up to `horizon`: tested where the trial balances
    allow, otherwise listed with its deadline and what is missing. `period_inputs`, `adjustments` and `leases` hold
    confirmed facts only."""
    period_inputs = period_inputs or {}
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
            facts = period_inputs.get(test_date, PeriodInput())
            f, problem = adjust(f, terms, adjustments, leases, lease_problem)
            results = tuple(apply_at_date(c, f, terms, facts.revolver_drawn, problem) for c in terms.covenants)
            breached = any(r.status == "breach" for r in results)
            cure = terms.cure_deadline(due, facts.delivered_on) if breached else None
            pending = breached and bool(terms.cure_days) and cure is None
            out.append(TestDate(test_date, due, results, cure, cure_pending=pending))
        test_date = add_quarters(test_date, 1)
    return out
