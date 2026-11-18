from decimal import Decimal

import pytest

from provenant.tax.engine import AllowanceAsset, AllowanceClass, LeaseYear, MappedLine, TaxCredit, compute
from provenant.tax.mapping import TaxTreatment as T, check_mapping, suggest

D = Decimal


def line(n, name, balance, fs_line, treatment):
    return MappedLine(f"tb{n}", str(n), name, D(balance), fs_line, treatment)


# A worked example, computed by hand (KES):
#   PBT = 1,000,000 + 15,000 - (400,000 + 200,000 + 50,000 + 10,000 + 80,000 + 20,000 + 12,000) = 243,000
#   + penalties 10,000 + depreciation 80,000 + lease interest 12,000 - dividends 15,000
#   - capital deduction 25% x (300,000 + 100,000) = 100,000 - lease rentals 40,000          = 190,000
#   - losses brought forward 50,000                                                           = 140,000
#   tax at 30%                                                                                 =  42,000
#   deferred: PPE (600,000 - 250,000) - (400,000 - 100,000) = 50,000; leases 120,000 - 130,000 = -10,000
#             net 40,000 x 30% = 12,000 liability
TB = [
    line(4000, "Revenue - consulting fees", -1_000_000, "revenue", T.TAXABLE),
    line(4100, "Dividend income", -15_000, "other_income", T.EXEMPT),
    line(5000, "Cost of sales", 400_000, "cost_of_sales", T.DEDUCTIBLE),
    line(5100, "Salaries and wages", 200_000, "employee_costs", T.DEDUCTIBLE),
    line(5200, "Office expenses", 50_000, "operating_expenses", T.DEDUCTIBLE),
    line(5300, "KRA penalties", 10_000, "operating_expenses", T.DISALLOWABLE),
    line(5400, "Depreciation", 80_000, "depreciation_amortisation", T.DEPRECIATION),
    line(5500, "Loan interest", 20_000, "finance_costs", T.DEDUCTIBLE),
    line(5600, "Interest on lease liabilities", 12_000, "finance_costs", T.LEASE_INTEREST),
    line(5900, "Income tax expense", 60_000, "income_tax_expense", T.TAX_CHARGE),
    line(1500, "Plant and equipment - cost", 600_000, "ppe_cost", T.BALANCE_SHEET),
    line(1510, "Accumulated depreciation", -250_000, "ppe_accumulated_depreciation", T.BALANCE_SHEET),
]
PLANT = AllowanceClass("Plant and machinery", D(300_000), D(100_000), D("0.25"), "reducing", "Second Schedule (as entered)")
LEASE = LeaseYear("lease-doc", "Office lease", D(40_000), D(30_000), D(12_000), D(120_000), D(130_000))


def test_worked_example():
    c = compute(TB, [PLANT], [LEASE], losses_brought_forward=D(50_000))
    assert c.profit_before_tax == D(243_000)
    assert {a.label: a.amount for a in c.adjustments} == {
        "Disallowable expenses added back": D(10_000),
        "Accounting depreciation and amortisation added back": D(80_000),
        "IFRS 16 lease interest added back": D(12_000),
        "Exempt income deducted": D(-15_000),
        "Capital deduction: Plant and machinery": D(-100_000),
        "Lease rentals paid in the year deducted": D(-40_000),
    }
    assert (c.taxable_before_losses, c.losses_used, c.taxable_profit) == (D(190_000), D(50_000), D(140_000))
    assert c.tax_payable == D(42_000) and c.losses_carried_forward == 0
    assert [d.amount for d in c.deferred] == [D(50_000), D(-10_000)]
    assert all(" " in d.basis for d in c.deferred)  # readable basis text, not an internal key
    assert c.deferred_tax == D(12_000)
    assert c.warnings == ()
    penalties = next(a for a in c.adjustments if a.label.startswith("Disallowable"))
    assert penalties.sources == ("tb5300",) and "s.16" in penalties.basis


def test_a_tax_loss_is_carried_forward_and_becomes_a_deferred_tax_asset():
    loss_tb = [line(1, "Revenue", -100_000, "revenue", T.TAXABLE), line(2, "Expenses", 160_000, "operating_expenses", T.DEDUCTIBLE)]
    c = compute(loss_tb, losses_brought_forward=D(10_000))
    assert c.taxable_before_losses == D(-60_000) and c.losses_used == 0
    assert c.tax_payable == 0 and c.losses_carried_forward == D(70_000)
    assert c.deferred_tax == D(-21_000)  # an asset, subject to recoverability


def test_lease_interest_without_linked_leases_is_flagged():
    c = compute([*TB], [PLANT])
    assert any("no leases are linked" in w for w in c.warnings)


def test_straight_line_class_never_takes_wdv_below_zero():
    c = AllowanceClass("Buildings", D(1_000), D(0), D("0.10"), "straight_line", "basis", cost_basis=D(50_000))
    assert c.allowance() == D(1_000) and c.wdv_carried_forward() == 0
    with pytest.raises(ValueError, match="basis"):
        AllowanceClass("Plant", D(0), D(0), D("0.25"), "reducing", " ")


@pytest.mark.parametrize(
    ("name", "line", "treatment"),
    [
        ("Accumulated depreciation - motor vehicles", "ppe_accumulated_depreciation", T.BALANCE_SHEET),
        ("Depreciation charge", "depreciation_amortisation", T.DEPRECIATION),
        ("Interest on lease liabilities", "finance_costs", T.LEASE_INTEREST),
        ("KRA penalties and fines", "operating_expenses", T.DISALLOWABLE),
        ("Donations - children's home", "operating_expenses", T.DISALLOWABLE),
        ("Dividend income", "other_income", T.EXEMPT),
        ("Equity Bank current account", "cash", T.BALANCE_SHEET),
        ("Staff salaries", "employee_costs", T.DEDUCTIBLE),
        ("Consulting fees earned", "revenue", T.TAXABLE),
        ("Cost of sales", "cost_of_sales", T.DEDUCTIBLE),
        ("Sales - Nairobi branch", "revenue", T.TAXABLE),
        # Found by a dry run on a realistic Kenyan trial balance: P&L signals must beat balance-sheet nouns.
        ("Term loan - Rift Valley Bank", "borrowings", T.BALANCE_SHEET),
        ("Depreciation - right-of-use asset", "depreciation_amortisation", T.DEPRECIATION),
        ("Interest on term loan", "finance_costs", T.DEDUCTIBLE),
        ("Interest income - call deposits", "other_income", T.TAXABLE),
        ("Bank charges", "finance_costs", T.DEDUCTIBLE),
        ("Office and administrative expenses", "operating_expenses", T.DEDUCTIBLE),
    ],
)
def test_keyword_suggestions(name, line, treatment):
    s = suggest(name)
    assert (s.fs_line, s.tax_treatment) == (line, treatment)


def test_unknown_accounts_are_not_guessed_and_mappings_must_be_consistent():
    assert suggest("Sundry 7").fs_line is None
    with pytest.raises(ValueError, match="does not apply"):
        check_mapping("cash", "deductible")
    with pytest.raises(ValueError, match="does not apply"):
        check_mapping("revenue", "balance_sheet")
    assert check_mapping("operating_expenses", "disallowable") is T.DISALLOWABLE


def test_disposal_proceeds_above_the_pool_are_a_balancing_charge():
    c = AllowanceClass("Motor vehicles", D(100_000), D(0), D("0.25"), "reducing", "basis", disposals=D(150_000))
    assert (c.allowance(), c.balancing_charge(), c.wdv_carried_forward()) == (0, D(50_000), 0)
    partial = AllowanceClass("Plant", D(300_000), D(100_000), D("0.25"), "reducing", "basis", disposals=D(20_000))
    assert (partial.allowance(), partial.balancing_charge(), partial.wdv_carried_forward()) == (D(95_000), 0, D(285_000))
    run = compute(TB, [PLANT, c], [LEASE], losses_brought_forward=D(50_000))
    charge = next(a for a in run.adjustments if a.label.startswith("Balancing charge"))
    assert charge.amount == D(50_000) and "Second Schedule" in charge.basis
    assert run.taxable_profit == D(190_000)  # 140,000 + 50,000


@pytest.mark.parametrize(
    ("proceeds", "label", "amount"),
    [
        (D(850), "Balancing charge on cessation: Plant · A-1", D(100)),
        (D(500), "Balancing deduction on cessation: Plant · A-1", D(-250)),
    ],
)
def test_cessation_balancing_outcome_uses_item_residual_value(proceeds, label, amount):
    # Hand-worked (KES): opening residual value 1,000; 25% allowance = 250; residual at cessation = 750.
    # Proceeds 850 produce a 100 charge; proceeds 500 produce a 250 balancing deduction.
    asset = AllowanceAsset("A-1", D(2_000), D(1_000), False, proceeds, True)
    plant = AllowanceClass("Plant", D(1_000), D(0), D("0.25"), "reducing", "Second Schedule para. 4",
                           assets=(asset,), business_ceased=True)
    result = compute([], [plant])
    adjustment = next(item for item in result.adjustments if item.label == label)

    assert adjustment.amount == amount
    assert adjustment.sources == ("asset:A-1",)
    assert "Second Schedule paragraph 4" in adjustment.basis
    assert not any(item.label.startswith(("Disposal gain", "Disposal loss")) for item in result.adjustments)


def test_cessation_balancing_requires_item_level_assets():
    class_only = AllowanceClass("Plant", D(1_000), D(0), D("0.25"), "reducing", "basis", business_ceased=True)
    with pytest.raises(ValueError, match="matched per-asset register"):
        compute([], [class_only])


def test_credits_take_the_tax_payable_to_tax_due_or_refundable():
    credits = [TaxCredit("instalment", "Q1-Q4 instalments", D(30_000), "KRA-PRN-1"),
               TaxCredit("withholding", "Certificate from Safari Bank", D(5_000), "WHT-77")]
    run = compute(TB, [PLANT], [LEASE], losses_brought_forward=D(50_000), credits=credits)
    assert run.tax_payable == D(42_000) and run.tax_due == D(7_000)
    assert [(l.label, l.amount, l.sources) for l in run.credits] == [
        ("Instalment tax paid", D(-30_000), ("credit:0",)), ("Withholding tax credits", D(-5_000), ("credit:1",))]
    refund = compute(TB, [PLANT], [LEASE], D(50_000), credits=[*credits, TaxCredit("overpayment", "FY2025", D(10_000))])
    assert refund.tax_due == D(-3_000)  # refundable
    with pytest.raises(ValueError, match="credit kind"):
        TaxCredit("rebate", "x", D(1))
    with pytest.raises(ValueError, match="positive"):
        TaxCredit("instalment", "x", D(0))
