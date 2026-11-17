"""IFRS 16 lease modifications: remeasure at the effective date with revised payments, term and rate."""
from collections import defaultdict
from datetime import date
from decimal import Decimal

import pytest

from provenant.core.money import ZERO, round_money
from provenant.leases import LeaseTerms, Modification, Timing, build_schedule, journal_entries
from provenant.leases.ifrs16 import (
    ACCUMULATED_DEPRECIATION,
    LEASE_LIABILITY,
    MODIFICATION_GAIN,
    ROU_ASSET,
)
from provenant.provenance import ConfidenceGate, ReviewLog, Status

BASE = LeaseTerms(date(2026, 1, 31), 12, Decimal("1000"), Decimal("0.01"))
AFTER_6 = date(2026, 7, 31)


def annuity(payment: str, n: int, rate: str, advance: bool = False) -> Decimal:
    """Independent closed form: P * (1 - (1+r)^-n) / r, times (1+r) when paid in advance."""
    p, r = Decimal(payment), Decimal(rate)
    value = p * (1 - (1 + r) ** -n) / r
    return round_money(value * (1 + r) if advance else value)


def balances(schedule) -> dict[str, Decimal]:
    totals = defaultdict(Decimal)
    for entry in journal_entries(schedule):
        for line in entry.lines:
            totals[line.account] += line.debit - line.credit
    return totals


def test_payment_increase_remeasures_at_the_revised_rate():
    s = build_schedule(BASE, [Modification(AFTER_6, Decimal("0.015"), payment=Decimal("1200"))])
    (r,) = s.remeasurements
    assert r.after_period == 6 and r.effective == AFTER_6
    assert r.liability_before == s.periods[5].closing
    assert r.liability_after == annuity("1200", 6, "0.015")
    assert r.rou_adjustment == r.adjustment and r.gain == 0  # an increase goes to the asset
    assert [p.payment for p in s.periods] == [Decimal("1000")] * 6 + [Decimal("1200")] * 6
    assert [p.segment for p in s.periods] == [0] * 6 + [1] * 6
    assert s.periods[6].opening == r.liability_after
    assert s.periods[-1].closing == 0


def test_extension_adds_periods_and_respreads_depreciation():
    s = build_schedule(BASE, [Modification(AFTER_6, Decimal("0.0125"), total_periods=18)])
    assert len(s.periods) == 18
    (r,) = s.remeasurements
    assert r.liability_after == annuity("1000", 12, "0.0125")
    rou_after = r.rou_before + r.rou_adjustment
    assert s.periods[6].depreciation == round_money(rou_after / 12)
    assert sum(p.depreciation for p in s.periods) == s.initial_rou_asset + r.rou_adjustment


@pytest.mark.parametrize("timing", list(Timing))
def test_chain_of_two_modifications_closes_and_balances(timing):
    terms = LeaseTerms(date(2026, 1, 31), 24, Decimal("1000"), Decimal("0.01"), timing)
    mods = [  # given out of order: the engine applies them by effective date
        Modification(date(2026, 1, 31).replace(year=2027), Decimal("0.02"), payment=Decimal("900")),
        Modification(date(2026, 6, 30), Decimal("0.015"), payment=Decimal("1100"), total_periods=30),
    ]
    s = build_schedule(terms, mods)
    assert [r.after_period for r in s.remeasurements] == [5, 12]
    assert len(s.periods) == 30 and s.periods[-1].closing == 0
    for prev, cur in zip(s.periods, s.periods[1:]):
        remeasured = next((r for r in s.remeasurements if r.after_period == prev.number), None)
        assert cur.opening == (remeasured.liability_after if remeasured else prev.closing)
    totals = balances(s)
    assert totals[LEASE_LIABILITY] == 0
    assert totals[ROU_ASSET] + totals[ACCUMULATED_DEPRECIATION] == 0


def test_decrease_beyond_the_asset_goes_to_profit_or_loss():
    # Large incentives leave a small ROU asset; a big rent cut then removes more liability than asset.
    terms = LeaseTerms(date(2026, 1, 31), 12, Decimal("1000"), Decimal("0.01"), incentives_received=Decimal("11000"))
    s = build_schedule(terms, [Modification(AFTER_6, Decimal("0.01"), payment=Decimal("100"))])
    (r,) = s.remeasurements
    assert r.rou_before + r.rou_adjustment == 0
    assert r.gain == -(r.adjustment - r.rou_adjustment) and r.gain > 0
    totals = balances(s)
    assert totals[LEASE_LIABILITY] == 0
    assert totals[MODIFICATION_GAIN] == -r.gain  # a credit
    assert totals[ROU_ASSET] + totals[ACCUMULATED_DEPRECIATION] == 0


@pytest.mark.parametrize(
    ("modifications", "message"),
    [
        ([Modification(date(2026, 7, 15), Decimal("0.01"), payment=Decimal("1"))], "period boundary"),
        ([Modification(date(2027, 3, 31), Decimal("0.01"), payment=Decimal("1"))], "period boundary"),
        ([Modification(AFTER_6, Decimal("0.01"), total_periods=6)], "terminations"),
        (
            [Modification(AFTER_6, Decimal("0.01"), payment=Decimal("1")),
             Modification(AFTER_6, Decimal("0.02"), payment=Decimal("2"))],
            "ambiguous",
        ),
    ],
)
def test_unsupported_or_ambiguous_modifications_are_refused(modifications, message):
    with pytest.raises(ValueError, match=message):
        build_schedule(BASE, modifications)


def test_modification_needs_a_change_and_verified_fields(make_field):
    with pytest.raises(ValueError, match="change the payment or the term"):
        Modification(AFTER_6, Decimal("0.01"))

    gate = ConfidenceGate("amendment-v1", 0.95, frozenset({"effective_date"}))
    fields = {
        n: gate.route(make_field(n, v))
        for n, v in {"effective_date": "2026-07-31", "annual_discount_rate": "18%", "monthly_payment": "1,200"}.items()
    }
    with pytest.raises(PermissionError, match="effective_date"):
        Modification.from_fields(fields)
    log = ReviewLog()
    fields["effective_date"] = log.record(fields["effective_date"], "partner", Status.APPROVED)
    m = Modification.from_fields(fields)
    assert (m.effective, m.payment, m.total_periods, m.rate_per_period) == (AFTER_6, Decimal("1200"), None, Decimal("0.015"))


def test_rejected_change_means_unchanged(make_field):
    gate = ConfidenceGate("amendment-v1", 0.95)
    fields = {
        n: gate.route(make_field(n, v))
        for n, v in {"effective_date": "2026-07-31", "annual_discount_rate": "12%", "term_months": "18"}.items()
    }
    fields["monthly_payment"] = ReviewLog().record(
        gate.route(make_field("monthly_payment", "9,999", 0.5)), "partner", Status.REJECTED
    )
    m = Modification.from_fields(fields)
    assert m.payment is None and m.total_periods == 18
