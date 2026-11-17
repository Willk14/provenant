"""IFRS 16 lessee accounting: lease liability, amortisation schedule, ROU asset, journal entries.

Pure and deterministic: the same verified terms always give the same schedule, to the cent.
"""
import calendar
from collections.abc import Mapping
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
            return fields[name].value.strip()

        rate = to_decimal(value("annual_discount_rate").rstrip("%"))
        if value("annual_discount_rate").endswith("%"):
            rate /= 100
        term = to_decimal(value("term_months"))
        if term != term.to_integral_value():
            raise ValueError("term_months must be a whole number")
        return cls(
            commencement=date.fromisoformat(value("commencement_date")),
            periods=int(term),
            payment=to_decimal(value("monthly_payment")),
            rate_per_period=rate / 12,
            timing=Timing(value("payment_timing").lower()),
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


@dataclass(frozen=True)
class LeaseSchedule:
    terms: LeaseTerms
    initial_liability: Decimal
    initial_rou_asset: Decimal
    periods: tuple[Period, ...]


def add_months(start: date, months: int) -> date:
    month_index = start.month - 1 + months
    year, month = start.year + month_index // 12, month_index % 12 + 1
    return date(year, month, min(start.day, calendar.monthrange(year, month)[1]))


def present_value(terms: LeaseTerms) -> Decimal:
    """L0 = sum of P / (1 + r)^t, with t = 1..n in arrears or 0..n-1 in advance. Unrounded."""
    first = 1 if terms.timing is Timing.ARREARS else 0
    with localcontext() as ctx:
        ctx.prec = 34
        factor = 1 + terms.rate_per_period
        return sum((terms.payment / factor**t for t in range(first, first + terms.periods)), ZERO)


def build_schedule(terms: LeaseTerms) -> LeaseSchedule:
    liability = round_money(present_value(terms))
    rou = liability + terms.initial_direct_costs + terms.prepaid_payments - terms.incentives_received
    if rou < 0:
        raise ValueError("incentives exceed the right-of-use asset")
    monthly_depreciation = round_money(rou / terms.periods)

    periods: list[Period] = []
    opening, depreciated = liability, ZERO
    for n in range(1, terms.periods + 1):
        last = n == terms.periods
        base = opening if terms.timing is Timing.ARREARS else opening - terms.payment
        # The final period absorbs rounding so the liability and the asset both end at exactly zero.
        interest = terms.payment - opening if last else round_money(base * terms.rate_per_period)
        depreciation = rou - depreciated if last else monthly_depreciation
        closing = opening + interest - terms.payment
        periods.append(
            Period(n, add_months(terms.commencement, n), opening, terms.payment, interest, closing, depreciation)
        )
        opening, depreciated = closing, depreciated + depreciation

    return LeaseSchedule(terms, liability, rou, tuple(periods))


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
    for p in schedule.periods:
        paid_on = add_months(terms.commencement, p.number - 1) if terms.timing is Timing.ADVANCE else p.end_date
        entries.append(JournalEntry(paid_on, f"Lease payment {p.number}", pair(LEASE_LIABILITY, CASH, p.payment)))
        lines = pair(INTEREST_EXPENSE, LEASE_LIABILITY, p.interest) + pair(
            DEPRECIATION_EXPENSE, ACCUMULATED_DEPRECIATION, p.depreciation
        )
        if lines:
            entries.append(JournalEntry(p.end_date, f"Lease interest and depreciation {p.number}", lines))
    return entries
