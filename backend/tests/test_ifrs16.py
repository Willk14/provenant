from collections import defaultdict
from datetime import date
from decimal import Decimal

import pytest

from provenant.core.money import ZERO
from provenant.leases import LeaseTerms, Timing, build_schedule, journal_entries, present_value
from provenant.leases.ifrs16 import (
    ACCUMULATED_DEPRECIATION,
    LEASE_LIABILITY,
    ROU_ASSET,
    add_months,
)
from provenant.provenance import ConfidenceGate, ReviewLog, Status


def terms(timing=Timing.ARREARS, rate="0.01", **extra):
    return LeaseTerms(date(2026, 1, 31), 12, Decimal("1000"), Decimal(rate), timing, **extra)


# Golden values: 1000 * (1 - 1.01^-12) / 0.01 = 11,255.08; in advance multiply by 1.01.
@pytest.mark.parametrize(("timing", "expected"), [(Timing.ARREARS, "11255.08"), (Timing.ADVANCE, "11367.63")])
def test_present_value_matches_annuity_formula(timing, expected):
    assert build_schedule(terms(timing)).initial_liability == Decimal(expected)


def test_zero_rate_is_sum_of_payments():
    assert present_value(terms(rate="0")) == Decimal("12000")


@pytest.mark.parametrize("timing", list(Timing))
def test_schedule_closes_to_exactly_zero(timing):
    s = build_schedule(terms(timing, initial_direct_costs=Decimal("500.00")))
    assert s.periods[-1].closing == ZERO
    assert sum(p.interest for p in s.periods) == Decimal("12000") - s.initial_liability
    assert sum(p.depreciation for p in s.periods) == s.initial_rou_asset
    assert s.initial_rou_asset == s.initial_liability + Decimal("500.00")
    for prev, cur in zip(s.periods, s.periods[1:]):
        assert cur.opening == prev.closing


@pytest.mark.parametrize("timing", list(Timing))
def test_journal_entries_net_to_zero_over_lease_life(timing):
    balances = defaultdict(Decimal)
    for entry in journal_entries(build_schedule(terms(timing, incentives_received=Decimal("250.00")))):
        for line in entry.lines:
            balances[line.account] += line.debit - line.credit
    assert balances[LEASE_LIABILITY] == ZERO
    assert balances[ROU_ASSET] + balances[ACCUMULATED_DEPRECIATION] == ZERO


def test_add_months_clamps_month_end():
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2026, 11, 30), 3) == date(2027, 2, 28)


def lease_fields(make_field, confidence=0.99):
    raw = {
        "commencement_date": "2026-01-31",
        "term_months": "12",
        "monthly_payment": "1,000.00",
        "annual_discount_rate": "12%",
        "payment_timing": "Arrears",
    }
    return {name: make_field(name, value, confidence) for name, value in raw.items()}


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("monthly_payment", "120,000120,000"),
        ("monthly_payment", "KES 1,000"),
        ("annual_discount_rate", "12"),  # no %: 12 or 0.12? refuse rather than guess
        ("annual_discount_rate", "0.12"),
        ("commencement_date", "31/01/2026"),
        ("commencement_date", "2026-02-30"),
        ("term_months", "12.5"),
        ("payment_timing", "monthly"),
    ],
)
def test_check_value_rejects_ambiguous_or_malformed(name, value):
    with pytest.raises(ValueError):
        LeaseTerms.check_value(name, value)


def test_check_value_accepts_canonical_forms():
    assert LeaseTerms.check_value("monthly_payment", " 1,000.00 ") == "1,000.00"
    assert LeaseTerms.check_value("annual_discount_rate", "8.5%") == "8.5%"
    assert LeaseTerms.check_value("payment_timing", "Arrears") == "Arrears"


def test_core_refuses_unverified_fields(make_field):
    gate = ConfidenceGate("lease-v1", threshold=0.95)
    routed = {k: gate.route(f) for k, f in lease_fields(make_field, confidence=0.5).items()}
    with pytest.raises(PermissionError, match="not verified"):
        LeaseTerms.from_fields(routed)
    with pytest.raises(PermissionError):
        LeaseTerms.from_fields(lease_fields(make_field))  # never routed at all


def test_extraction_to_schedule_end_to_end(make_field):
    gate = ConfidenceGate("lease-v1", threshold=0.95, always_review=frozenset({"payment_timing"}))
    log = ReviewLog()
    routed = {k: gate.route(f) for k, f in lease_fields(make_field).items()}
    routed["payment_timing"] = log.record(routed["payment_timing"], "partner@firm", Status.APPROVED)

    t = LeaseTerms.from_fields(routed)
    assert t.rate_per_period == Decimal("0.01") and t.periods == 12 and t.timing is Timing.ARREARS
    assert build_schedule(t).initial_liability == Decimal("11255.08")
    assert log.verify()
