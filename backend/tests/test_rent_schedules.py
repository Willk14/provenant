"""Escalating rent and rent-free months, in the engine and in the terms layer."""
from collections import defaultdict
from datetime import date
from decimal import Decimal, localcontext

import pytest

from provenant.core.formats import check_value
from provenant.core.money import round_money
from provenant.leases import LeaseTerms, Modification, Timing, build_schedule, journal_entries
from provenant.leases.ifrs16 import ACCUMULATED_DEPRECIATION, LEASE_LIABILITY, ROU_ASSET, resolve_free, resolve_steps
from provenant.provenance import ConfidenceGate, ReviewLog, Status

R = Decimal("0.01")
START = date(2026, 1, 31)


def annuity(payment, n, rate=R):
    """Unrounded closed form P * (1 - (1+r)^-n) / r: independent of the engine's period-by-period sum."""
    with localcontext() as ctx:
        ctx.prec = 34
        return Decimal(payment) * (1 - (1 + rate) ** -n) / rate


def discount(value, periods, rate=R):
    with localcontext() as ctx:
        ctx.prec = 34
        return value / (1 + rate) ** periods


def balances(schedule):
    totals = defaultdict(Decimal)
    for entry in journal_entries(schedule):
        for line in entry.lines:
            totals[line.account] += line.debit - line.credit
    return totals


def test_escalation_matches_two_discounted_annuities():
    terms = LeaseTerms(START, 24, Decimal("1000"), R, steps=((13, Decimal("1030")),))
    expected = annuity(1000, 12) + discount(annuity(1030, 12), 12)
    s = build_schedule(terms)
    assert s.initial_liability == round_money(expected)
    assert [p.payment for p in s.periods] == [Decimal("1000")] * 12 + [Decimal("1030")] * 12
    assert s.periods[-1].closing == 0


@pytest.mark.parametrize("timing", list(Timing))
def test_rent_free_months_pay_nothing_but_accrue_interest(timing):
    terms = LeaseTerms(START, 12, Decimal("1000"), R, timing, free_periods=frozenset({1, 2}))
    s = build_schedule(terms)
    # Ten payments after two free months; paid in advance, each is one period earlier (times 1 + r).
    expected = discount(annuity(1000, 10), 2) * ((1 + R) if timing is Timing.ADVANCE else 1)
    assert s.initial_liability == round_money(expected)
    assert [p.payment for p in s.periods[:3]] == [0, 0, Decimal("1000")]
    assert s.periods[0].interest > 0  # the liability still unwinds while no rent is paid
    assert s.periods[-1].closing == 0
    payment_memos = [e.memo for e in journal_entries(s) if e.memo.startswith("Lease payment")]
    assert "Lease payment 1" not in payment_memos and len(payment_memos) == 10
    totals = balances(s)
    assert totals[LEASE_LIABILITY] == 0 and totals[ROU_ASSET] + totals[ACCUMULATED_DEPRECIATION] == 0


def test_amendment_with_its_own_escalating_and_free_rent():
    base = LeaseTerms(START, 12, Decimal("1000"), R)
    mod = Modification(
        date(2026, 7, 31), Decimal("0.015"), payment=Decimal("1200"), total_periods=18,
        steps=((7, Decimal("1260")),), free_periods=frozenset({1}),
    )
    s = build_schedule(base, [mod])
    payments = [p.payment for p in s.periods]
    assert payments[:6] == [Decimal("1000")] * 6
    assert payments[6:] == [0] + [Decimal("1200")] * 5 + [Decimal("1260")] * 6  # periods 7..18
    r15 = Decimal("0.015")
    expected = discount(annuity(1200, 5, r15), 1, r15) + discount(annuity(1260, 6, r15), 6, r15)
    (remeasurement,) = s.remeasurements
    assert remeasurement.liability_after == round_money(expected)
    assert s.periods[-1].closing == 0
    assert balances(s)[LEASE_LIABILITY] == 0


def test_term_only_extension_keeps_the_lease_escalations():
    base = LeaseTerms(START, 12, Decimal("1000"), R, steps=((7, Decimal("1100")),))
    s = build_schedule(base, [Modification(date(2026, 4, 30), R, total_periods=18)])
    assert [p.payment for p in s.periods] == [Decimal("1000")] * 6 + [Decimal("1100")] * 12


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        (dict(steps=((13, Decimal("1")),)), "term has 12 periods"),
        (dict(steps=((5, Decimal("2")), (3, Decimal("1")))), "increasing order"),
        (dict(steps=((1, Decimal("2")),)), "after period 1"),
        (dict(steps=((5, Decimal("0")),)), "rent-free periods for zero"),
        (dict(free_periods=frozenset({13})), "term has 12 periods"),
    ],
)
def test_invalid_schedules_are_refused(kwargs, message):
    with pytest.raises(ValueError, match=message):
        LeaseTerms(START, 12, Decimal("1000"), R, **kwargs)


def test_amendment_rent_must_fit_its_remaining_term():
    base = LeaseTerms(START, 12, Decimal("1000"), R)
    mod = Modification(date(2026, 7, 31), R, payment=Decimal("1200"), steps=((9, Decimal("1300")),))
    with pytest.raises(ValueError, match="term has 6 periods"):
        build_schedule(base, [mod])
    with pytest.raises(ValueError, match="needs the new monthly payment"):
        Modification(date(2026, 7, 31), R, total_periods=18, steps=((2, Decimal("1")),))


def test_dates_become_periods_counted_from_the_start_date():
    # Month-end commencement: period boundaries clamp (31 Jan -> 28 Feb -> 31 Mar ...).
    assert resolve_steps("2027-01-31: 1,030.00; 2026-02-28: 1,010", START) == (
        (2, Decimal("1010")), (13, Decimal("1030.00")))
    assert resolve_free("2026-03, 1", START) == frozenset({3, 1})
    with pytest.raises(ValueError, match="not the start of a monthly period"):
        resolve_steps("2026-02-15: 1,000", START)
    with pytest.raises(ValueError, match="before"):
        resolve_free("2025-12", START)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("rent_steps", "13 1030"),
        ("rent_steps", "13: KES 1,030"),
        ("rent_steps", "13: 1,030; 13: 1,040"),
        ("rent_steps", "month 13: 1,030"),
        ("free_periods", "April 2025"),
        ("free_periods", "2025-13"),
        ("free_periods", "1, 1"),
    ],
)
def test_schedule_formats_reject_ambiguous_input(name, value):
    with pytest.raises(ValueError):
        check_value(name, value)


def test_lease_terms_from_fields_use_verified_schedule_terms(make_field):
    gate = ConfidenceGate("lease-v1", 0.95)
    raw = {
        "commencement_date": "2026-01-31", "term_months": "24", "monthly_payment": "1,000",
        "annual_discount_rate": "12%", "payment_timing": "arrears",
        "rent_steps": "13: 1,030", "free_periods": "1",
    }
    fields = {n: gate.route(make_field(n, v)) for n, v in raw.items()}
    fields["rent_steps"] = gate.route(make_field("rent_steps", "13: 1,030", confidence=0.5))
    with pytest.raises(PermissionError, match="rent_steps"):
        LeaseTerms.from_fields(fields)
    log = ReviewLog()
    fields["rent_steps"] = log.record(fields["rent_steps"], "partner", Status.APPROVED)
    terms = LeaseTerms.from_fields(fields)
    assert terms.steps == ((13, Decimal("1030")),) and terms.free_periods == frozenset({1})
    # Rejecting a proposed schedule term means the lease has none.
    rejected = dict(fields)
    rejected["free_periods"] = log.record(
        gate.route(make_field("free_periods", "1", confidence=0.5)), "partner", Status.REJECTED
    )
    assert LeaseTerms.from_fields(rejected).free_periods == frozenset()
