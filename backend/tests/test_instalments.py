from datetime import date
from decimal import Decimal

from provenant.tax.instalments import plan_instalments

D = Decimal


def test_standard_installments_use_lower_basis_and_allocate_paid_amounts():
    # Hand: min(KES 120,000 current tax, 110% x 100,000 prior tax) = 110,000.
    # Four 25% instalments are KES 27,500; a KES 30,000 payment on the first due date covers
    # instalment 1 and KES 2,500 of instalment 2. June year end means October balance deadline.
    plan = plan_instalments(date(2026, 6, 30), D("120000"), D("100000"), D("0"), D("0"),
                            ((date(2025, 10, 20), D("30000")),))
    assert plan.annual_amount == D("110000.00")
    assert plan.basis_used == "110% of prior-year assessment"
    assert [x.due_date for x in plan.installments] == [date(2025, 10, 20), date(2025, 12, 20), date(2026, 3, 20), date(2026, 6, 20)]
    assert [x.paid_to_date for x in plan.installments] == [D("27500.00"), D("2500.00"), D("0"), D("0")]
    assert plan.balance_due_date == date(2026, 10, 31)


def test_agriculture_proportion_and_withholding_reduce_instalment_base():
    # Hand: min(120,000, 110% x 150,000) - 20,000 withholding = 100,000;
    # over two-thirds agricultural income: 75,000 due in month 9, 25,000 in month 12.
    plan = plan_instalments(date(2026, 12, 31), D("120000"), D("150000"), D("20000"), D("0.70"))
    assert plan.annual_amount == D("100000.00")
    assert [x.amount for x in plan.installments] == [D("75000.00"), D("25000.00")]
    assert [x.due_date for x in plan.installments] == [date(2026, 9, 20), date(2026, 12, 20)]


def test_two_thirds_boundary_is_not_agricultural_schedule_and_rounding_reconciles():
    # At exactly two-thirds, the ordinary four-quarter schedule applies. Final period absorbs cents.
    plan = plan_instalments(date(2026, 12, 31), D("100.01"), None, D("0"), D(2) / D(3))
    assert [x.amount for x in plan.installments] == [D("25.00"), D("25.00"), D("25.00"), D("25.01")]
    assert sum((x.amount for x in plan.installments), D(0)) == D("100.01")
