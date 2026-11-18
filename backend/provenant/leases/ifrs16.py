"""IFRS 16 lessee accounting: lease liability, amortisation schedule, ROU asset, journal entries.

Pure and deterministic: the same verified terms always give the same schedule, to the cent.
"""
import calendar
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, localcontext
from enum import StrEnum

from provenant.core.formats import FORMATS, check_value, parse_free_periods, parse_rent_steps
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


Steps = tuple[tuple[int, Decimal], ...]  # (from period, amount per period from then on)


@dataclass(frozen=True)
class RentSchedule:
    """Rent per period: a first amount, step changes (escalations), and rent-free periods.

    Periods are numbered from 1 within the schedule's own run: the lease's term, or for an amendment the
    periods after its effective date. Real leases escalate and abate; one fixed payment rarely fits (ADR 0005).
    """

    first: Decimal
    steps: Steps = ()
    free: frozenset[int] = frozenset()

    def __post_init__(self) -> None:
        if self.first <= 0:
            raise ValueError("the first rent amount must be positive")
        starts = [n for n, _ in self.steps]
        if any(n < 2 for n in starts) or starts != sorted(set(starts)):
            raise ValueError("rent steps must start after period 1, in increasing order, once each")
        if any(amount <= 0 for _, amount in self.steps):
            raise ValueError("stepped rent must be positive; use rent-free periods for zero rent")
        if any(n < 1 for n in self.free):
            raise ValueError("rent-free periods are numbered from 1")

    def amount(self, n: int) -> Decimal:
        if n in self.free:
            return ZERO
        current = self.first
        for start, amount in self.steps:
            if start > n:
                break
            current = amount
        return current

    def check_within(self, periods: int, what: str) -> None:
        late = [n for n, _ in self.steps if n > periods] + [n for n in self.free if n > periods]
        if late:
            raise ValueError(f"{what}: rent changes in period {min(late)} but the term has {periods} periods")


@dataclass(frozen=True)
class LeaseTerms:
    commencement: date
    periods: int  # months
    payment: Decimal  # rent for the first period (before any step)
    rate_per_period: Decimal  # incremental borrowing rate per month
    timing: Timing = Timing.ARREARS
    initial_direct_costs: Decimal = ZERO
    prepaid_payments: Decimal = ZERO
    incentives_received: Decimal = ZERO
    steps: Steps = ()  # escalations, by lease period
    free_periods: frozenset[int] = frozenset()  # rent-free (abated) lease periods

    def __post_init__(self) -> None:
        if self.periods < 1:
            raise ValueError("lease needs at least one period")
        if self.payment <= 0:
            raise ValueError("payment must be positive")
        if self.rate_per_period < 0:
            raise ValueError("rate must be non-negative")
        if min(self.initial_direct_costs, self.prepaid_payments, self.incentives_received) < 0:
            raise ValueError("costs, prepayments and incentives must be non-negative")
        self.rent.check_within(self.periods, "lease")

    @property
    def rent(self) -> RentSchedule:
        return RentSchedule(self.payment, self.steps, self.free_periods)

    REQUIRED = ("commencement_date", "term_months", "monthly_payment", "annual_discount_rate", "payment_timing")
    OPTIONAL = ("rent_steps", "free_periods")
    FIELDS = REQUIRED + OPTIONAL
    FORMATS = FORMATS  # shared shapes, see provenant.core.formats

    @staticmethod
    def check_value(name: str, value: str) -> str:
        return check_value(name, value)

    @classmethod
    def from_fields(cls, fields: Mapping[str, ExtractedField]) -> "LeaseTerms":
        """Build terms from extracted fields. Refuses anything not yet verified (ADR 0001). Optional schedule
        terms must be verified too if present; a rejected one is treated as absent (no steps, no free months).

        shortcut: monthly payments only, rate converted as annual / 12; add other frequencies when needed.
        """
        missing = [name for name in cls.REQUIRED if name not in fields]
        if missing:
            raise ValueError(f"missing lease fields: {', '.join(missing)}")
        present = [n for n in cls.FIELDS if n in fields and not (n in cls.OPTIONAL and fields[n].status == "rejected")]
        unverified = [n for n in present if not fields[n].verified]
        if unverified:
            raise PermissionError(f"fields not verified: {', '.join(unverified)}")

        def value(name: str) -> str | None:
            return cls.check_value(name, fields[name].value) if name in present else None

        commencement = date.fromisoformat(value("commencement_date"))
        return cls(
            commencement=commencement,
            periods=int(value("term_months")),
            payment=to_decimal(value("monthly_payment")),
            rate_per_period=monthly_rate(value("annual_discount_rate")),
            timing=Timing(value("payment_timing").lower()),
            steps=resolve_steps(value("rent_steps"), commencement),
            free_periods=resolve_free(value("free_periods"), commencement),
        )


def _period_starting(start: date, on: date, what: str) -> int:
    """The period (1-based) that starts on `on`, counting monthly from `start`."""
    for k in range(0, 1200):
        boundary = add_months(start, k)
        if boundary == on:
            return k + 1
        if boundary > on:
            break
    raise ValueError(f"{what} {on} is not the start of a monthly period counted from {start}")


def resolve_steps(text: str | None, start: date) -> Steps:
    """'13: 1,030.00; 2026-06-01: 1,060.90' -> ((13, 1030.00), (k, 1060.90)), dates counted from `start`."""
    if not text:
        return ()
    steps = []
    for when, amount in parse_rent_steps(text):
        period = when if isinstance(when, int) else _period_starting(start, when, "rent step on")
        steps.append((period, amount))
    return tuple(sorted(steps))


def resolve_free(text: str | None, start: date) -> frozenset[int]:
    """'1, 2' or '2025-04, 2025-05' -> {1, 2}: months counted from `start`."""
    if not text:
        return frozenset()
    periods = set()
    for month in parse_free_periods(text):
        if isinstance(month, int):
            periods.add(month)
            continue
        year, number = month
        k = (year - start.year) * 12 + number - start.month
        if k < 0:
            raise ValueError(f"rent-free month {year}-{number:02d} is before {start}")
        periods.add(k + 1)
    return frozenset(periods)




def monthly_rate(annual_percent: str) -> Decimal:
    """shortcut: monthly payments only, nominal annual rate / 12; add other frequencies when a lease needs them."""
    return to_decimal(annual_percent.rstrip("%")) / 100 / 12


@dataclass(frozen=True)
class Modification:
    """A lease modification that is not a separate lease (IFRS 16.44-46).

    Two dates matter (ADR 0005 finding 4). `effective` is the modification's effective date: when the parties
    agree, usually the signing date. The liability is remeasured then, at the revised rate, over the revised
    payments. `terms_start` is when the new rent or extended term begins; it may be months later (an extension
    signed in May that starts in September). Until then the old rent continues.

    shortcut: the remeasurement is posted at the first period boundary on or after the agreement date, so
    interest for the days in between accrues at the previous rate; the schedule records both dates. New terms
    must start on a period boundary. No decreases in scope (partial terminations) or period-specific rent
    deferrals: both need their own treatment (IFRS 16.46(a), rent-concession guidance) and are refused.
    """

    effective: date
    rate_per_period: Decimal  # revised discount rate, required for a modification (IFRS 16.45(c))
    payment: Decimal | None = None  # new rent for the first period from `terms_start`, if it changes
    total_periods: int | None = None  # new total term in months from the original commencement, if it changes
    steps: Steps = ()  # escalations of the new rent, by period counted from `terms_start`
    free_periods: frozenset[int] = frozenset()  # rent-free periods counted from `terms_start`
    terms_start: date | None = None  # when the new rent or term starts; defaults to the effective date
    expiry: date | None = None  # new expiry date: how amendments usually state the term (finding 5)

    REQUIRED = ("effective_date", "annual_discount_rate")
    CHANGES = ("monthly_payment", "term_months", "expiry_date", "rent_steps", "free_periods")
    FIELDS = REQUIRED + ("terms_start_date",) + CHANGES

    def __post_init__(self) -> None:
        if self.payment is None and self.total_periods is None and self.expiry is None:
            raise ValueError("an amendment must change the payment or the term")
        if self.payment is None and (self.steps or self.free_periods):
            raise ValueError("a new rent schedule needs the new monthly payment it starts from")
        if self.payment is not None and self.payment <= 0:
            raise ValueError("payment must be positive")
        if self.rate_per_period < 0:
            raise ValueError("rate must be non-negative")
        if self.terms_start is not None and self.terms_start < self.effective:
            raise ValueError(f"new terms start {self.terms_start} before the amendment is agreed ({self.effective})")

    @property
    def starts(self) -> date:
        return self.terms_start or self.effective

    @property
    def rent(self) -> RentSchedule | None:
        """The new rent, counted from when the new terms start; None when the rent carries on unchanged."""
        return None if self.payment is None else RentSchedule(self.payment, self.steps, self.free_periods)

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
        effective = date.fromisoformat(value("effective_date"))
        starts = date.fromisoformat(s) if (s := value("terms_start_date")) else None
        expiry = date.fromisoformat(e) if (e := value("expiry_date")) else None
        return cls(
            effective=effective,
            rate_per_period=monthly_rate(value("annual_discount_rate")),
            payment=to_decimal(payment) if payment else None,
            total_periods=int(term) if term else None,
            steps=resolve_steps(value("rent_steps"), starts or effective),
            free_periods=resolve_free(value("free_periods"), starts or effective),
            terms_start=starts,
            expiry=expiry,
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
    remeasured_on: date | None = None  # the period boundary it is posted at (on or after `effective`)
    terms_start: date | None = None  # when the new rent or term starts

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


def _pv(payments: Sequence[Decimal], rate: Decimal, timing: Timing) -> Decimal:
    """sum of P_t / (1 + r)^t over the payments in order, with t = 1..n in arrears or 0..n-1 in advance."""
    first = 1 if timing is Timing.ARREARS else 0
    with localcontext() as ctx:
        ctx.prec = 34
        factor = 1 + rate
        return sum((p / factor ** (first + i) for i, p in enumerate(payments)), ZERO)


def present_value(terms: LeaseTerms) -> Decimal:
    rent = terms.rent
    return _pv([rent.amount(n) for n in range(1, terms.periods + 1)], terms.rate_per_period, terms.timing)


def _remeasure_after(commencement: date, effective: date, total: int) -> int:
    """The period a modification is remeasured after: the first boundary on or after it is agreed."""
    for k in range(1, total):
        if add_months(commencement, k) >= effective:
            return k
    raise ValueError(
        f"amendment agreed {effective} is not before the last period of the lease "
        f"(which starts {add_months(commencement, total - 1)}); an agreement after the term is a new lease"
    )


def _boundary(commencement: date, when: date, first: int, last: int, what: str) -> int:
    """The period boundary `when` falls on, between periods `first` and `last` of the lease."""
    for k in range(first, last + 1):
        if add_months(commencement, k) == when:
            return k
    raise ValueError(
        f"{what} {when} must be a period boundary between {add_months(commencement, first)} and "
        f"{add_months(commencement, last)}; mid-period changes are not supported yet"
    )


def _expiry_periods(commencement: date, expiry: date) -> int:
    """Months from commencement to an expiry date, stated either as the last day or the boundary itself."""
    for k in range(1, 12 * 100):
        end = add_months(commencement, k)
        if expiry in (end, end - timedelta(days=1)):
            return k
        if end > expiry:
            break
    raise ValueError(
        f"new expiry date {expiry} is not at the end of a whole period from commencement {commencement}"
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
    rate, total = terms.rate_per_period, terms.periods
    pay = terms.rent.amount  # rent for an absolute lease period; replaced when an amendment changes the rent
    opening, rou_left, start = liability, rou, 0

    for segment, modification in enumerate([*ordered, None]):
        stop = total if modification is None else _remeasure_after(terms.commencement, modification.effective, total)
        if stop <= start:
            raise ValueError(f"amendment effective {modification.effective} is not after the previous one")
        per_period = round_money(rou_left / (total - start))
        for n in range(start + 1, stop + 1):
            payment = pay(n)
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
        if modification.expiry is not None:
            by_expiry = _expiry_periods(terms.commencement, modification.expiry)
            if modification.total_periods is not None and modification.total_periods != by_expiry:
                raise ValueError(f"amendment agreed {modification.effective}: a {modification.total_periods}-month "
                                 f"term and expiry {modification.expiry} ({by_expiry} months) disagree")
            new_total = by_expiry
        if new_total <= stop:
            raise ValueError(f"amendment agreed {modification.effective} ends the lease; terminations are not supported")
        # New rent from the period that starts on `terms_start`, which may be as late as the end of the current
        # term (an extension); the old rent runs until then. A gap after expiry would be a new lease.
        rent_from = _boundary(terms.commencement, modification.starts, stop, min(total, new_total - 1),
                              "new terms start")
        if (new_rent := modification.rent) is not None:
            new_rent.check_within(new_total - rent_from, f"amendment agreed {modification.effective}")
            pay = lambda n, rent=new_rent, offset=rent_from, old=pay: (  # noqa: E731
                old(n) if n <= offset else rent.amount(n - offset))
        rate, total = modification.rate_per_period, new_total
        remeasured = round_money(_pv([pay(n) for n in range(stop + 1, total + 1)], rate, terms.timing))
        rou_adjustment = remeasured - opening
        gain = ZERO
        if rou_left + rou_adjustment < 0:  # cannot take the asset below zero; the rest is a gain (IFRS 16.39)
            gain, rou_adjustment = -(rou_left + rou_adjustment), -rou_left
        remeasurements.append(Remeasurement(
            stop, modification.effective, opening, remeasured, rou_left, rou_adjustment, gain,
            remeasured_on=add_months(terms.commencement, stop), terms_start=modification.starts,
        ))
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
        if p.payment:  # nothing is paid in a rent-free period
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
