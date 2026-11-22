from datetime import date
from decimal import Decimal

import pytest

from provenant.covenants import Category, Covenant, Kind, PeriodInput, TBLine, Terms, TrialBalance, evaluate, figures, run
from provenant.covenants.engine import add_business_days, quarters_back
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
