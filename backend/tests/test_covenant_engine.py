from datetime import date
from decimal import Decimal

import pytest

from provenant.covenants import Category, Covenant, Kind, PeriodInput, TBLine, Terms, TrialBalance, evaluate, figures, run
from provenant.core.formats import check_value, parse_relative_test, parse_threshold_steps
from provenant.covenants.engine import (
    Adjustment,
    FrozenLease,
    add_business_days,
    adjust,
    quarters_back,
    resolve_first_test,
)
from provenant.provenance import ConfidenceGate, ReviewLog, Status

M = Decimal("1000000")


def tb(period_end: date, *, revenue, opex, interest, cash, borrowings, leases=0, da=0) -> TrialBalance:
    """A quarterly TB in millions, signed debit-positive, balanced by an 'other' line."""
    lines = [
        TBLine("4000", "Revenue", Category.REVENUE, -Decimal(revenue) * M),
        TBLine("5000", "Operating expenses", Category.OPERATING_EXPENSES, Decimal(opex) * M),
        TBLine("6100", "Interest", Category.INTEREST_EXPENSE, Decimal(interest) * M),
        TBLine("6200", "Depreciation", Category.DEPRECIATION_AMORTISATION, Decimal(da) * M),
        TBLine("1000", "Bank", Category.CASH, Decimal(cash) * M),
        TBLine("2500", "Term loan", Category.BORROWINGS, -Decimal(borrowings) * M),
        TBLine("2600", "Lease liabilities", Category.LEASE_LIABILITIES, -Decimal(leases) * M),
    ]
    balance = sum(line.balance for line in lines)
    lines.append(TBLine("3000", "Equity and other net assets", Category.OTHER, -balance))
    return TrialBalance(period_end, tuple(lines))


QUARTERS = [date(2026, 3, 31), date(2026, 6, 30), date(2026, 9, 30), date(2026, 12, 31)]
STEADY = [tb(d, revenue=300, opex=225, interest=18, cash=120, borrowings=900, leases=60, da=30) for d in QUARTERS]
COVENANTS = (
    Covenant(Kind.LEVERAGE, Decimal("3.50")),
    Covenant(Kind.INTEREST_COVER, Decimal("4.00")),
    Covenant(Kind.MIN_LIQUIDITY, Decimal("50") * M),
)


def test_trial_balance_must_balance():
    with pytest.raises(ValueError, match="does not balance"):
        TrialBalance(date(2026, 3, 31), (TBLine("1", "Bank", Category.CASH, Decimal("1")),))


def test_quarter_ends_handle_month_lengths():
    assert quarters_back(date(2026, 3, 31), 4) == [date(2025, 6, 30), date(2025, 9, 30), date(2025, 12, 31), date(2026, 3, 31)]


def test_ltm_sums_four_quarters_and_reads_balance_sheet_at_the_test_date():
    f = figures({t.period_end: t for t in STEADY}, date(2026, 12, 31))
    assert f.ebitda == 300 * M  # 4 x (300 - 225); depreciation and interest are excluded
    assert f.net_debt == 840 * M  # 900 + 60 - 120, at 31 Dec only
    assert f.totals[Category.INTEREST_EXPENSE] == 72 * M
    with pytest.raises(LookupError, match="needs trial balances for 2027-03-31$"):
        figures({t.period_end: t for t in STEADY}, date(2027, 3, 31))


def test_each_covenant_with_exact_comparison_and_headroom():
    f = figures({t.period_end: t for t in STEADY}, date(2026, 12, 31))
    leverage, cover, liquidity = (evaluate(c, f, Decimal("0.10")) for c in COVENANTS)
    assert leverage.value == Decimal("2.8") and leverage.status == "pass"
    assert leverage.headroom == 1 - Decimal("2.8") / Decimal("3.5")  # EBITDA could fall 20%
    assert cover.value == Decimal(300) / Decimal(72) and cover.status == "warning"  # 4.17x: within 10% of 4.00x
    assert liquidity.value == 120 * M and liquidity.status == "pass"
    assert [w.label for w in leverage.workings][:4] == ["Borrowings", "Lease liabilities", "Cash", "Net debt"]


def test_exactly_at_the_threshold_passes_and_one_cent_over_breaches():
    def leverage_at(net_debt_m):
        tbs = [tb(d, revenue=100, opex=75, interest=1, cash=0, borrowings=net_debt_m) for d in QUARTERS]
        return evaluate(Covenant(Kind.LEVERAGE, Decimal("3.5")), figures({t.period_end: t for t in tbs}, QUARTERS[-1]), Decimal(0))

    assert leverage_at(Decimal("350")).status == "pass"  # 350 / 100 == 3.5 exactly
    assert leverage_at(Decimal("350.00000001")).status == "breach"


def test_negative_ebitda_is_a_leverage_breach_not_a_negative_ratio():
    tbs = [tb(d, revenue=100, opex=120, interest=5, cash=10, borrowings=200) for d in QUARTERS]
    r = evaluate(COVENANTS[0], figures({t.period_end: t for t in tbs}, QUARTERS[-1]), Decimal("0.1"))
    assert r.status == "breach" and r.value is None and "not positive" in r.note


def test_run_lists_due_dates_cure_windows_and_missing_history():
    weak = tb(date(2027, 3, 31), revenue=260, opex=235, interest=20, cash=60, borrowings=950, leases=58)
    terms = Terms(COVENANTS, first_test_date=date(2026, 12, 31), reporting_days=45, cure_days=20)
    dates = run(terms, [*STEADY, weak], horizon=date(2027, 6, 30))
    assert [d.period_end for d in dates] == [date(2026, 12, 31), date(2027, 3, 31), date(2027, 6, 30)]
    first, second, future = dates
    assert first.certificate_due == date(2027, 2, 14) and first.cure_deadline is None
    assert {r.covenant.kind: r.status for r in second.results} == {
        Kind.LEVERAGE: "breach",  # 948 / 250 = 3.79x
        Kind.INTEREST_COVER: "breach",  # 250 / 74 = 3.38x
        Kind.MIN_LIQUIDITY: "pass",
    }
    assert second.certificate_due == date(2027, 5, 15) and second.cure_deadline == date(2027, 6, 4)
    assert future.results == () and "2027-06-30" in future.missing


@pytest.mark.parametrize("full, effective, year_end, expected", [
    # December year: quarters end Mar, Jun, Sep, Dec.
    (True, date(2026, 2, 15), 12, date(2026, 6, 30)),  # Q1 began 1 Jan, before the date, so Q2 is the first full one
    (False, date(2026, 2, 15), 12, date(2026, 3, 31)),  # Q1 ends after 15 Feb
    (True, date(2026, 1, 1), 12, date(2026, 3, 31)),  # Q1 starts on the date, so all of it is covered
    (False, date(2026, 3, 31), 12, date(2026, 6, 30)),  # "ending after": 31 Mar itself does not count
    (True, date(2026, 12, 31), 12, date(2027, 3, 31)),  # crosses the year
    # June year: quarters end Sep, Dec, Mar, Jun. 15 Feb is in the quarter 1 Jan-31 Mar, so the next full one ends June.
    (True, date(2026, 2, 15), 6, date(2026, 6, 30)),
    # February year: quarters end May, Aug, Nov, Feb; 29 Feb 2028 is a quarter-end, so the next ends 31 May.
    (False, date(2028, 2, 29), 2, date(2028, 5, 31)),
    # November year: quarters end Feb, May, Aug, Nov. 10 Jan falls in 1 Dec-28 Feb, so the next full one ends 31 May.
    (True, date(2026, 1, 10), 11, date(2026, 5, 31)),
])
def test_relative_first_test_date(full, effective, year_end, expected):
    assert resolve_first_test(full, effective, year_end) == expected


def test_relative_rule_format():
    rule = "First full quarter after 2026-02-15, year end December"
    assert check_value("first_test_date", rule) == rule
    assert parse_relative_test(rule) == (True, date(2026, 2, 15), 12)
    assert parse_relative_test("first quarter after 2026-02-15, year end june") == (False, date(2026, 2, 15), 6)
    assert parse_relative_test("2026-12-31") is None
    for bad in ("first full quarter after 2026-02-30, year end December", "first quarter after closing",
                "first quarter after 2026-02-15, year end Smarch"):
        with pytest.raises(ValueError):
            check_value("first_test_date", bad)


def test_threshold_step_format():
    assert parse_threshold_steps("2027-12-31: 3.50x; 2027-03-31: 3.75x", ratio=True) == [
        (date(2027, 3, 31), Decimal("3.75")), (date(2027, 12, 31), Decimal("3.50"))]
    assert parse_threshold_steps("2027-03-31: 60,000,000", ratio=False) == [(date(2027, 3, 31), 60 * M)]
    for bad in ("2027-03-31: 3.75", "March: 3.75x", "2027-03-31: 3.75x; 2027-03-31: 3.50x"):
        with pytest.raises(ValueError):
            check_value("max_leverage_steps", bad)


def test_threshold_steps_apply_from_their_test_date_until_the_next():
    # Every quarter: net debt 840, LTM EBITDA 300, so leverage is 2.80x throughout.
    quarters = [date(2026, 3, 31), date(2026, 6, 30), date(2026, 9, 30), date(2026, 12, 31),
                date(2027, 3, 31), date(2027, 6, 30), date(2027, 9, 30)]
    tbs = [tb(d, revenue=300, opex=225, interest=1, cash=120, borrowings=900, leases=60) for d in quarters]
    terms = Terms((Covenant(Kind.LEVERAGE, Decimal("3.00")),), date(2026, 12, 31), 45, threshold_steps=(
        (Kind.LEVERAGE, date(2027, 3, 31), Decimal("2.75")),  # 2.80 > 2.75: breach
        (Kind.LEVERAGE, date(2027, 6, 30), Decimal("3.50")),  # 2.80 <= 3.50: headroom 1 - 2.8/3.5 = 20%
    ))
    december, march, june, september = [t.results[0] for t in run(terms, tbs, date(2027, 9, 30))]
    assert (december.covenant.threshold, december.status, december.note) == (Decimal("3.00"), "warning", "")
    assert (march.covenant.threshold, march.status) == (Decimal("2.75"), "breach")
    assert march.note == "Threshold 2.75x applies from 2027-03-31"
    assert (june.covenant.threshold, june.status, june.headroom) == (Decimal("3.50"), "pass", Decimal("0.2"))
    assert september.covenant.threshold == Decimal("3.50")  # the last step stays in force
    assert terms.field_at(Kind.LEVERAGE, date(2026, 12, 31)) == "max_leverage_ratio"
    assert terms.field_at(Kind.LEVERAGE, date(2027, 3, 31)) == "max_leverage_steps"


@pytest.mark.parametrize("kind, on, level, message", [
    (Kind.INTEREST_COVER, date(2027, 3, 31), "4.00", "base covenant"),
    (Kind.LEVERAGE, date(2026, 12, 31), "3.00", "after the first test date"),
    (Kind.LEVERAGE, date(2027, 1, 31), "3.00", "not a quarterly test date"),
    (Kind.LEVERAGE, date(2027, 3, 30), "3.00", "not a quarterly test date"),
    (Kind.LEVERAGE, date(2027, 3, 31), "0", "more than zero"),
])
def test_bad_threshold_steps_are_refused(kind, on, level, message):
    with pytest.raises(ValueError, match=message):
        Terms((Covenant(Kind.LEVERAGE, Decimal("3.50")),), date(2026, 12, 31), 45,
              threshold_steps=((kind, on, Decimal(level)),))


def _addback(quarter, millions, capped=True, source="a1"):
    return Adjustment(quarter, "Restructuring", Decimal(millions) * M, capped, source)


def test_an_addback_counts_in_every_twelve_months_containing_its_quarter():
    # 75 a quarter is 300 LTM. A 30 add-back in Q1 2026 is in the LTM to Dec 2026 (330) and has left by Mar 2027.
    quarters = [*QUARTERS, date(2027, 3, 31)]
    tbs = {d: tb(d, revenue=300, opex=225, interest=18, cash=120, borrowings=900) for d in quarters}
    terms = Terms((Covenant(Kind.LEVERAGE, Decimal("3.5")),), date(2026, 12, 31), 45)
    item = _addback(date(2026, 3, 31), 30, capped=False)
    december, _ = adjust(figures(tbs, date(2026, 12, 31)), terms, [item], [])
    march, _ = adjust(figures(tbs, date(2027, 3, 31)), terms, [item], [])
    assert december.ebitda == 330 * M and march.ebitda == 300 * M


@pytest.mark.parametrize("after, expected", [
    # Base 300, capped add-backs 100. Before add-backs: 20% x 300 = 60 allowed, so 360.
    (False, Decimal("360")),
    # After add-backs: A <= 20% x (300 + A), so A <= 300 x 0.2 / 0.8 = 75, so 375.
    (True, Decimal("375")),
])
def test_addback_cap_before_and_after(after, expected):
    f = figures({t.period_end: t for t in STEADY}, date(2026, 12, 31))
    terms = Terms(COVENANTS, date(2026, 12, 31), 45, addback_cap=Decimal("0.2"), cap_after_addbacks=after)
    adjusted, problem = adjust(f, terms, [_addback(date(2026, 6, 30), 100)], [])
    assert adjusted.ebitda == expected * M and problem is None
    assert adjusted.ebitda_lines[-1].label == "Add-backs above the cap"


def test_uncapped_items_raise_the_cap_base_and_escape_the_cap():
    # Base 300 + uncapped 50 = 350; cap 10% = 35 of the 100 capped; EBITDA 350 + 35 = 385.
    f = figures({t.period_end: t for t in STEADY}, date(2026, 12, 31))
    terms = Terms(COVENANTS, date(2026, 12, 31), 45, addback_cap=Decimal("0.1"))
    items = [_addback(date(2026, 6, 30), 100), _addback(date(2026, 9, 30), 50, capped=False, source="a2")]
    assert adjust(f, terms, items, [])[0].ebitda == 385 * M


def test_negative_ebitda_allows_no_capped_addbacks():
    tbs = {d: tb(d, revenue=100, opex=120, interest=5, cash=10, borrowings=200) for d in QUARTERS}
    terms = Terms(COVENANTS, date(2026, 12, 31), 45, addback_cap=Decimal("0.2"))
    # Reported -80; the cap on a negative base is zero, so the 30 add-back is wholly removed.
    assert adjust(figures(tbs, QUARTERS[-1]), terms, [_addback(QUARTERS[0], 30)], [])[0].ebitda == -80 * M


def _lease() -> FrozenLease:
    # Twelve months from Jan 2026: nothing for 3 months, then 40 a month (360 in total), so 30 a month straight-line.
    # Interest is 1 a month; the liability falls by 10 a month from 120.
    return FrozenLease("Office", "lease-1", tuple(
        (date(2026, m, 28), (Decimal(0) if m <= 3 else Decimal(40)) * M, M, Decimal(120 - 10 * m) * M)
        for m in range(1, 13)))


def test_frozen_gaap_uses_straight_line_rent_and_removes_lease_debt_and_interest():
    tbs = {d: tb(d, revenue=300, opex=225, interest=18, cash=120, borrowings=900, leases=60) for d in QUARTERS}
    terms = Terms(COVENANTS, date(2026, 12, 31), 45, frozen_gaap=True)
    f, problem = adjust(figures(tbs, QUARTERS[-1]), terms, [], [_lease()])
    assert problem is None
    assert f.ebitda == (300 - 360) * M  # 12 x 30 of operating lease charge against EBITDA of 300
    assert f.net_debt == (900 + 60 - 120) * M  # the closing liability on 28 Dec is 120 - 120 = 0
    assert f.interest == (72 - 12) * M


def test_frozen_gaap_removes_the_liability_at_the_test_date():
    # At 30 Sep the schedule's closing liability is 120 - 90 = 30, so net debt is 900 + 60 - 30 - 120 = 810.
    quarters = [date(2025, 12, 31), date(2026, 3, 31), date(2026, 6, 30), date(2026, 9, 30)]
    tbs = {d: tb(d, revenue=300, opex=225, interest=18, cash=120, borrowings=900, leases=60) for d in quarters}
    terms = Terms(COVENANTS, date(2026, 9, 30), 45, frozen_gaap=True)
    f, problem = adjust(figures(tbs, date(2026, 9, 30)), terms, [], [_lease()])
    assert problem is None and f.net_debt == 810 * M
    assert f.ebitda == (300 - 270) * M  # nine months at 30
    assert f.interest == (72 - 9) * M


@pytest.mark.parametrize("leases, problem, message", [
    ([], None, "add and approve the leases"),
    ([_lease()], "lease.pdf is not verified", "not verified"),
])
def test_frozen_gaap_never_passes_on_figures_it_cannot_rely_on(leases, problem, message):
    terms = Terms(COVENANTS, date(2026, 12, 31), 45, frozen_gaap=True)
    results = run(terms, STEADY, date(2026, 12, 31), leases=leases, lease_problem=problem)[0].results
    by_kind = {r.covenant.kind: r for r in results}
    assert by_kind[Kind.LEVERAGE].status == by_kind[Kind.INTEREST_COVER].status == "undetermined"
    assert message in by_kind[Kind.LEVERAGE].note and by_kind[Kind.LEVERAGE].value is None
    assert by_kind[Kind.MIN_LIQUIDITY].status == "pass"  # cash is not affected by lease accounting


def test_lease_schedule_above_the_trial_balance_is_undetermined():
    # The schedule's liability at 30 Sep is 30, but the trial balance holds only 10 of lease liabilities.
    quarters = [date(2025, 12, 31), date(2026, 3, 31), date(2026, 6, 30), date(2026, 9, 30)]
    tbs = {d: tb(d, revenue=300, opex=225, interest=18, cash=120, borrowings=900, leases=10) for d in quarters}
    terms = Terms(COVENANTS, date(2026, 9, 30), 45, frozen_gaap=True)
    assert "exceed" in adjust(figures(tbs, date(2026, 9, 30)), terms, [], [_lease()])[1]


def test_an_adjustment_off_the_test_cycle_is_a_problem_not_ignored():
    f = figures({t.period_end: t for t in STEADY}, date(2026, 12, 31))
    terms = Terms(COVENANTS, date(2026, 12, 31), 45)
    assert "2026-11-30" in adjust(f, terms, [_addback(date(2026, 11, 30), 5)], [])[1]


def test_terms_from_fields_need_verification_and_a_covenant(make_field):
    gate = ConfidenceGate("covenant-v1", 0.95, frozenset({"max_leverage_ratio"}))
    raw = {"max_leverage_ratio": "3.50x", "first_test_date": "2026-12-31", "reporting_days": "45"}
    fields = {n: gate.route(make_field(n, v)) for n, v in raw.items()}
    with pytest.raises(PermissionError, match="max_leverage_ratio"):
        Terms.from_fields(fields)
    log = ReviewLog()
    fields["max_leverage_ratio"] = log.record(fields["max_leverage_ratio"], "partner", Status.APPROVED)
    terms = Terms.from_fields(fields)
    assert terms.covenants == (Covenant(Kind.LEVERAGE, Decimal("3.50")),) and terms.cure_days is None

    rejected = dict(fields)
    rejected["max_leverage_ratio"] = log.record(
        gate.route(make_field("max_leverage_ratio", "9.99x")), "partner", Status.REJECTED
    )
    with pytest.raises(ValueError, match="at least one financial covenant"):
        Terms.from_fields(rejected)


def springing_terms(**kw):
    return Terms((Covenant(Kind.LEVERAGE, Decimal("2.00")), Covenant(Kind.MIN_LIQUIDITY, Decimal("50") * M)),
                 date(2026, 12, 31), 45, cure_days=10, springing_usage=Decimal("0.35"),
                 revolver_commitment=100 * M, **kw)


def leverage_at(terms, drawn=None, delivered=None):
    inputs = {date(2026, 12, 31): PeriodInput(drawn, delivered)}
    test = run(terms, STEADY, date(2026, 12, 31), inputs)[0]
    return test, next(r for r in test.results if r.covenant.kind is Kind.LEVERAGE)


def test_springing_leverage_is_tested_only_above_the_usage_trigger():
    # Trigger: 35% x 100,000,000 = 35,000,000. Net debt 840m / EBITDA 300m = 2.80x, above the 2.00x limit.
    terms = springing_terms()
    _, unknown = leverage_at(terms)
    assert unknown.status == "undetermined" and unknown.value is None  # never assumed "not triggered"
    test, at_trigger = leverage_at(terms, 35 * M)  # "exceeds": exactly 35% is not tested
    assert at_trigger.status == "not_tested" and "35.00%" in at_trigger.note and test.cure_deadline is None
    test, above = leverage_at(terms, 35 * M + Decimal("0.01"))
    assert above.status == "breach" and above.value == Decimal("2.8")
    assert [w.label for w in above.workings[:3]] == ["Revolver drawn", "Revolver commitment", "Test trigger"]
    # The liquidity covenant does not spring: cash 120m against 50m is tested throughout.
    assert next(r for r in test.results if r.covenant.kind is Kind.MIN_LIQUIDITY).status == "pass"


def test_cure_window_in_business_days_from_due_date_or_delivery():
    assert add_business_days(date(2027, 2, 12), 1) == date(2027, 2, 15)  # Friday to Monday
    # Due Sunday 14 Feb 2027. Ten weekdays: 15-19 Feb (5), 22-26 Feb (10), so Friday 26 Feb.
    test, _ = leverage_at(springing_terms(cure_business_days=True), 90 * M)
    assert test.certificate_due == date(2027, 2, 14) and test.cure_deadline == date(2027, 2, 26)
    # From delivery on Wednesday 10 Feb: 11-12 (2), 15-19 (7), 22-24 (10), so Wednesday 24 Feb.
    from_delivery = springing_terms(cure_business_days=True, cure_from_delivery=True)
    test, _ = leverage_at(from_delivery, 90 * M, date(2027, 2, 10))
    assert test.cure_deadline == date(2027, 2, 24) and not test.cure_pending
    # Delivery not known: no deadline is invented, and the breach says so.
    test, _ = leverage_at(from_delivery, 90 * M)
    assert test.cure_deadline is None and test.cure_pending


def test_springing_terms_must_be_complete():
    with pytest.raises(ValueError, match="both"):
        Terms(COVENANTS, date(2026, 12, 31), 45, springing_usage=Decimal("0.35"))
    with pytest.raises(ValueError, match="no?ne|leverage"):
        Terms((Covenant(Kind.MIN_LIQUIDITY, M),), date(2026, 12, 31), 45, springing_usage=Decimal("0.35"),
              revolver_commitment=M)
    with pytest.raises(ValueError, match="cure period"):
        Terms(COVENANTS, date(2026, 12, 31), 45, cure_business_days=True)
