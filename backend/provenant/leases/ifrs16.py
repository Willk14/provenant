"""IFRS 16 lessee accounting: lease liability, amortisation schedule, ROU asset, journal entries.

Pure and deterministic: the same verified terms always give the same schedule, to the cent.
"""
import calendar
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext
from enum import StrEnum

from provenant.core.money import ZERO, round_money, to_decimal
from provenant.ledger import JournalEntry, pair
from provenant.provenance import ExtractedField

ROU_ASSET = "Right-of-use asset"
ACCUMULATED_DEPRECIATION = "Accumulated depreciation - ROU"
LEASE_LIABILITY = "Lease liability"
INTEREST_EXPENSE = "Interest expense - leases"
DEPRECIATION_EXPENSE = "Depreciation expense - ROU"
CASH = "Cash"
MODIFICATION_GAIN = "Gain on lease modification"


class Timing(StrEnum):
    ARREARS = "arrears"  # paid at the end of each period
    ADVANCE = "advance"  # paid at the start of each period


@dataclass(frozen=True)
class LeaseTerms:
    commencement: date
    periods: int  # months
    payment: Decimal  # fixed payment per period
    rate_per_period: Decimal  # incremental borrowing rate per month
    timing: Timing = Timing.ARREARS
    initial_direct_costs: Decimal = ZERO
    prepaid_payments: Decimal = ZERO
    incentives_received: Decimal = ZERO

    def __post_init__(self) -> None:
        if self.periods < 1:
            raise ValueError("lease needs at least one period")
        if self.payment <= 0:
            raise ValueError("payment must be positive")
        if self.rate_per_period < 0:
            raise ValueError("rate must be non-negative")
        if min(self.initial_direct_costs, self.prepaid_payments, self.incentives_received) < 0:
            raise ValueError("costs, prepayments and incentives must be non-negative")

    REQUIRED = ("commencement_date", "term_months", "monthly_payment", "annual_discount_rate", "payment_timing")
    FORMATS = None  # set below the class: shared with Modification

    @staticmethod
    def check_value(name: str, value: str) -> str:
        return check_value(name, value)

    @classmethod
    def from_fields(cls, fields: Mapping[str, ExtractedField]) -> "LeaseTerms":
        """Build terms from extracted fields. Refuses anything not yet verified (ADR 0001).

        shortcut: monthly payments only, rate converted as annual / 12; add other frequencies with M2.
        """
        missing = [name for name in cls.REQUIRED if name not in fields]
        if missing:
            raise ValueError(f"missing lease fields: {', '.join(missing)}")
        unverified = [name for name in cls.REQUIRED if not fields[name].verified]
        if unverified:
            raise PermissionError(f"fields not verified: {', '.join(unverified)}")

        def value(name: str) -> str:
            return cls.check_value(name, fields[name].value)

        return cls(
            commencement=date.fromisoformat(value("commencement_date")),
            periods=int(value("term_months")),
            payment=to_decimal(value("monthly_payment")),
            rate_per_period=monthly_rate(value("annual_discount_rate")),
            timing=Timing(value("payment_timing").lower()),
        )


# What a verified value must look like. Applied to human input (entry, corrections) and again before calculating.
# The rate must carry "%": a bare "12" vs "0.12" is exactly the ambiguity that ends up 100x wrong.
FORMATS = {
    "commencement_date": (r"\d{4}-\d{2}-\d{2}", "a date as YYYY-MM-DD"),
    "effective_date": (r"\d{4}-\d{2}-\d{2}", "a date as YYYY-MM-DD"),
    "term_months": (r"\d{1,3}", "a whole number of months"),
    "monthly_payment": (r"\d{1,3}(,\d{3})*(\.\d{1,2})?|\d+(\.\d{1,2})?", "an amount like 120,000 or 120000.00"),
    "annual_discount_rate": (r"\d{1,2}(\.\d+)?%", "a percentage like 12% or 8.5%"),
    "payment_timing": (r"(?i)advance|arrears", "advance or arrears"),
}
LeaseTerms.FORMATS = FORMATS


def check_value(name: str, value: str) -> str:
    """The value, stripped, if it has the right shape for `name`; otherwise ValueError saying what is expected."""
    if name not in FORMATS:
        raise ValueError(f"unknown lease field {name!r}")
    pattern, expected = FORMATS[name]
    value = value.strip()
    if not re.fullmatch(pattern, value):
        raise ValueError(f"{name.replace('_', ' ')} must be {expected}, got {value!r}")
    if name.endswith("_date"):
        date.fromisoformat(value)  # also rejects 2026-02-30
    return value


def monthly_rate(annual_percent: str) -> Decimal:
    """shortcut: monthly payments only, nominal annual rate / 12; add other frequencies when a lease needs them."""
    return to_decimal(annual_percent.rstrip("%")) / 100 / 12


@dataclass(frozen=True)
class Modification:
    """A lease modification that is not a separate lease (IFRS 16.44-46): from `effective`, the remaining
    payments are remeasured at a revised discount rate. Changes apply uniformly from that date.

    shortcut: no decreases in scope (partial terminations), no period-specific rent deferrals; both need
    their own treatment (IFRS 16.46(a) and the rent-concession guidance) and are refused rather than guessed.
    """

    effective: date
    rate_per_period: Decimal  # revised discount rate, required for a modification (IFRS 16.45(c))
    payment: Decimal | None = None  # new fixed payment per period, if it changes
    total_periods: int | None = None  # new total term in months from the original commencement, if it changes

    REQUIRED = ("effective_date", "annual_discount_rate")
    CHANGES = ("monthly_payment", "term_months")
    FIELDS = REQUIRED + CHANGES

    def __post_init__(self) -> None:
        if self.payment is None and self.total_periods is None:
            raise ValueError("an amendment must change the payment or the term")
        if self.payment is not None and self.payment <= 0:
            raise ValueError("payment must be positive")
        if self.rate_per_period < 0:
            raise ValueError("rate must be non-negative")

    @classmethod
    def from_fields(cls, fields: Mapping[str, ExtractedField]) -> "Modification":
        """Required terms must be verified. A proposed change must be verified too, or rejected: rejecting a
        proposed change records that the amendment does not change that term (it is then ignored)."""
        missing = [name for name in cls.REQUIRED if name not in fields]
        if missing:
            raise ValueError(f"missing amendment fields: {', '.join(missing)}")
        present = [n for n in cls.FIELDS if n in fields and not (n in cls.CHANGES and fields[n].status == "rejected")]
        unverified = [n for n in present if not fields[n].verified]
        if unverified:
            raise PermissionError(f"fields not verified: {', '.join(unverified)}")

        def value(name: str) -> str | None:
            return check_value(name, fields[name].value) if name in present else None

        payment, term = value("monthly_payment"), value("term_months")
        return cls(
            effective=date.fromisoformat(value("effective_date")),
            rate_per_period=monthly_rate(value("annual_discount_rate")),
            payment=to_decimal(payment) if payment else None,
            total_periods=int(term) if term else None,
        )


@dataclass(frozen=True)
class Period:
    number: int
    end_date: date
    opening: Decimal
    payment: Decimal
    interest: Decimal
    closing: Decimal
    depreciation: Decimal
    segment: int = 0  # 0 = original terms; n = after the n-th modification


@dataclass(frozen=True)
class Remeasurement:
    after_period: int
    effective: date
    liability_before: Decimal
    liability_after: Decimal
    rou_before: Decimal  # carrying amount just before
    rou_adjustment: Decimal
    gain: Decimal  # a decrease larger than the ROU carrying amount goes to profit or loss (IFRS 16.39)

    @property
    def adjustment(self) -> Decimal:
        return self.liability_after - self.liability_before


@dataclass(frozen=True)
class LeaseSchedule:
    terms: LeaseTerms
    initial_liability: Decimal
    initial_rou_asset: Decimal
    periods: tuple[Period, ...]
    remeasurements: tuple[Remeasurement, ...] = ()


def add_months(start: date, months: int) -> date:
    month_index = start.month - 1 + months
    year, month = start.year + month_index // 12, month_index % 12 + 1
    return date(year, month, min(start.day, calendar.monthrange(year, month)[1]))


def _pv(payment: Decimal, periods: int, rate: Decimal, timing: Timing) -> Decimal:
    """sum of P / (1 + r)^t, with t = 1..n in arrears or 0..n-1 in advance. Unrounded."""
    first = 1 if timing is Timing.ARREARS else 0
    with localcontext() as ctx:
        ctx.prec = 34
        factor = 1 + rate
        return sum((payment / factor**t for t in range(first, first + periods)), ZERO)


def present_value(terms: LeaseTerms) -> Decimal:
    return _pv(terms.payment, terms.periods, terms.rate_per_period, terms.timing)


def _boundary(commencement: date, effective: date, total: int) -> int:
    """Which period a modification takes effect after. It must fall on a period boundary inside the term."""
    for k in range(1, total):
        if add_months(commencement, k) == effective:
            return k
    raise ValueError(
        f"effective date {effective} must be a period boundary inside the remaining term "
        f"(e.g. {add_months(commencement, 1)}); mid-period modifications are not supported yet"
    )


def build_schedule(terms: LeaseTerms, modifications: Sequence[Modification] = ()) -> LeaseSchedule:
    """The schedule under the original terms, remeasured at each modification in effective-date order."""
    liability = round_money(present_value(terms))
    rou = liability + terms.initial_direct_costs + terms.prepaid_payments - terms.incentives_received
    if rou < 0:
        raise ValueError("incentives exceed the right-of-use asset")

    ordered = sorted(modifications, key=lambda m: m.effective)
    for a, b in zip(ordered, ordered[1:]):
        if a.effective == b.effective:
            raise ValueError(f"two amendments take effect on {a.effective}; their order is ambiguous")

    periods: list[Period] = []
    remeasurements: list[Remeasurement] = []
    payment, rate, total = terms.payment, terms.rate_per_period, terms.periods
    opening, rou_left, start = liability, rou, 0

    for segment, modification in enumerate([*ordered, None]):
        stop = total if modification is None else _boundary(terms.commencement, modification.effective, total)
        if stop <= start:
            raise ValueError(f"amendment effective {modification.effective} is not after the previous one")
        per_period = round_money(rou_left / (total - start))
        for n in range(start + 1, stop + 1):
            last = n == total
            base = opening if terms.timing is Timing.ARREARS else opening - payment
            # The final period absorbs rounding so the liability and the asset both end at exactly zero.
            interest = payment - opening if last else round_money(base * rate)
            depreciation = rou_left if last else per_period
            closing = opening + interest - payment
            periods.append(
                Period(n, add_months(terms.commencement, n), opening, payment, interest, closing, depreciation, segment)
            )
            opening, rou_left = closing, rou_left - depreciation

        if modification is None:
            break
        new_total = modification.total_periods or total
        if new_total <= stop:
            raise ValueError(f"amendment effective {modification.effective} ends the lease; terminations are not supported")
        payment = modification.payment or payment
        rate, total = modification.rate_per_period, new_total
        remeasured = round_money(_pv(payment, total - stop, rate, terms.timing))
        rou_adjustment = remeasured - opening
        gain = ZERO
        if rou_left + rou_adjustment < 0:  # cannot take the asset below zero; the rest is a gain (IFRS 16.39)
            gain, rou_adjustment = -(rou_left + rou_adjustment), -rou_left
        remeasurements.append(
            Remeasurement(stop, modification.effective, opening, remeasured, rou_left, rou_adjustment, gain)
        )
        opening, rou_left, start = remeasured, rou_left + rou_adjustment, stop

    return LeaseSchedule(terms, liability, rou, tuple(periods), tuple(remeasurements))


def journal_entries(schedule: LeaseSchedule) -> list[JournalEntry]:
    terms = schedule.terms
    cash_at_start = terms.initial_direct_costs + terms.prepaid_payments - terms.incentives_received
    entries = [
        JournalEntry(
            terms.commencement,
            "Initial recognition of lease",
            pair(ROU_ASSET, LEASE_LIABILITY, schedule.initial_liability) + pair(ROU_ASSET, CASH, cash_at_start),
        )
    ]
    remeasured_after = {r.after_period: r for r in schedule.remeasurements}
    for p in schedule.periods:
        paid_on = add_months(terms.commencement, p.number - 1) if terms.timing is Timing.ADVANCE else p.end_date
        entries.append(JournalEntry(paid_on, f"Lease payment {p.number}", pair(LEASE_LIABILITY, CASH, p.payment)))
        lines = pair(INTEREST_EXPENSE, LEASE_LIABILITY, p.interest) + pair(
            DEPRECIATION_EXPENSE, ACCUMULATED_DEPRECIATION, p.depreciation
        )
        if lines:
            entries.append(JournalEntry(p.end_date, f"Lease interest and depreciation {p.number}", lines))
        if r := remeasured_after.get(p.number):
            # Increase: Dr ROU / Cr liability. Decrease: Dr liability / Cr ROU, and Cr gain for any excess.
            lines = pair(ROU_ASSET, LEASE_LIABILITY, r.rou_adjustment) + pair(LEASE_LIABILITY, MODIFICATION_GAIN, r.gain)
            if lines:
                entries.append(JournalEntry(r.effective, f"Lease remeasurement after period {p.number}", lines))
    return entries
