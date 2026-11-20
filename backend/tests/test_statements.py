from decimal import Decimal

import pytest

from provenant.tax.statements import categorise, consolidate, read_bank, read_mpesa, totals

D = Decimal

# The portal export: summary lines, then the table, newest first. Opening balance 10,000.
MPESA = b"""Organization Name: KILIMA ANALYTICS LTD
Statement period: 01/03/2026 - 31/03/2026
Receipt No.,Completion Time,Details,Transaction Status,Paid In,Withdrawn,Balance,Other Party Info
SC4X5,2026-03-05 18:02:11,Pay merchant Charge,Completed,,30.00,57970.00,
SC4X4,2026-03-05 18:02:11,Business Payment to Rubis Energy,Completed,,2000.00,58000.00,Rubis Energy
SC2B2,2026-03-02 09:00:00,Transfer to Bank Account - Equity,Completed,,50000.00,60000.00,
SC1Z9,2026-03-01 16:45:00,Customer Payment to Small Business,Failed,500.00,,110000.00,
SC1A1,2026-03-01 10:00:00,Customer Payment to Small Business,Completed,100000.00,,110000.00,JANE W
"""

# Oldest first, debit and credit columns. The 3 March credit is the M-Pesa sweep. Row 5's balance is wrong.
BANK = b"""Date,Description,Reference,Debit,Credit,Balance
2026-03-01,Opening,,,,"200,000.00"
2026-03-03,MPESA SETTLEMENT KILIMA,FT26062ABC,,50000.00,250000.00
2026-03-10,KRA DOMESTIC TAX PRN 2026-03,FT26069KRA,40000.00,,210000.00
2026-03-25,SALARY MARCH 2026,FT26084SAL,150000.00,,59000.00
2026-03-31,LEDGER FEE,CHG0331,500.00,,58500.00
"""


def test_parse_dedupe_pair_and_check_balances():
    mpesa = read_mpesa(MPESA, "M-Pesa Till 123456")
    bank = read_bank(BANK, "Equity Bank 0123")
    assert len(mpesa) == 4  # the failed attempt never moved money
    assert [t.amount for t in mpesa] == [D("-30.00"), D(-2000), D(-50_000), D(100_000)]
    again = read_mpesa(MPESA, "M-Pesa Till 123456 (re-export)")  # the same receipts uploaded twice
    result = consolidate([mpesa, bank, again])
    assert len(result.duplicates) == 4 and len(result.txns) == 9

    # Sweep: M-Pesa -50,000 on 2 March pairs with the bank's +50,000 on 3 March
    ((out, into),) = result.transfers
    assert (out.reference, into.reference, into.amount) == ("SC2B2", "FT26062ABC", D(50_000))

    # M-Pesa balances run back from 57,970 to 110,000 cleanly. The bank's salary row: 210,000 - 150,000 = 60,000,
    # stated 59,000 (a missing or altered row of 1,000); the next row then follows from 59,000.
    assert [(b.source, b.row, b.expected, b.stated, b.gap) for b in result.breaks] == [
        ("Equity Bank 0123", 5, D(60_000), D(59_000), D(-1_000))]


def test_categories_are_suggested_and_a_person_confirms():
    result = consolidate([read_mpesa(MPESA, "M-Pesa"), read_bank(BANK, "Equity")])
    items = categorise(result, {"mpesa:sc4x4": ("supplier_payment", "pat")})
    by_ref = {c.txn.reference or c.txn.description: (c.category, c.status) for c in items}
    assert by_ref == {
        "SC4X5": ("charges", "suggested"),
        "SC4X4": ("supplier_payment", "confirmed"),
        "SC2B2": ("transfer", "suggested"),  # paired with the bank credit
        "SC1A1": ("sales_receipt", "suggested"),
        "FT26062ABC": ("transfer", "suggested"),
        "FT26069KRA": ("tax_payment", "suggested"),
        "FT26084SAL": ("salaries", "suggested"),
        "CHG0331": ("charges", "suggested"),
    }
    sums = totals(items)
    assert sums["sales_receipt"] == (D(100_000), D(0))
    assert sums["transfer"] == (D(50_000), D(50_000))  # in and out cancel: not income
    assert sums["charges"] == (D(0), D("530.00"))  # 30 M-Pesa + 500 ledger fee


def test_statements_are_checked():
    with pytest.raises(ValueError, match="columns needed"):
        read_mpesa(b"Receipt No.,Amount\nX,1\n", "m")
    with pytest.raises(ValueError, match="unreadable date"):
        read_bank(b"Date,Description,Amount\n31.03.26,x,1\n", "b")
