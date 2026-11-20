from datetime import date
from decimal import Decimal

import pytest

from provenant.tax.variance import paye, read_paye, read_vat, read_wht, vat_turnover, wht_on_payments, year_months

D = Decimal
MONTHS = year_months(date(2026, 1, 1), date(2026, 12, 31))

VAT = b"""period,standard_rated_sales,zero_rated_sales,exempt_sales
2026-01,"80,000",10000,0
2026-02,85000,0,5000
2026-03,90000,0,0
2025-12,99999,0,0
"""


def test_vat_turnover_against_revenue_with_missing_months():
    periods = read_vat(VAT)
    c = vat_turnover(D(272_000), periods, MONTHS)
    # Declared in 2026: 90,000 + 90,000 + 90,000 = 270,000; December 2025 is outside the year.
    # Variance 272,000 - 270,000 = 2,000; tolerance max(1,000, 0.5% x 272,000 = 1,360) = 1,360 -> explain.
    assert (c.declared, c.variance, c.tolerance, c.needs_explanation) == (D(270_000), D(2_000), D(1_360), True)
    assert c.notes == ("9 of 12 VAT returns missing: 2026-04, 2026-05, 2026-06, 2026-07, 2026-08, 2026-09, "
                       "2026-10, 2026-11, 2026-12", "VAT returns outside the year ignored: 2025-12")
    assert not vat_turnover(D(271_000), periods, MONTHS).needs_explanation  # 1,000 within 1,355


def test_paye_against_staff_costs():
    periods = read_paye(b"period,gross_pay,paye\n2026-01,400000,95000\n2026-02,400000,95000\n")
    # Staff costs 860,000 include employer NSSF and medical: 860,000 - 800,000 = 60,000 to explain
    c = paye(D(860_000), periods, MONTHS)
    assert (c.declared, c.variance, c.needs_explanation) == (D(800_000), D(60_000), True)


def test_wht_certificates_against_supplier_payments():
    certs = read_wht(b"""certificate_number,withholdee_pin,withholdee_name,date,gross_amount,wht_amount
WHT-1,P051111111A,Mwangi Advocates,2026-03-31,"200,000",10000
WHT-2,P051111111A,Mwangi Advocates,2026-06-30,100000,5000
WHT-3,P052222222B,Audit Partners,2026-12-15,500000,25000
WHT-0,P052222222B,Audit Partners,2025-12-15,1,0
""")
    payments = [("P051111111A", "Mwangi Advocates", D(300_000)), ("P052222222B", "Audit Partners", D(450_000)),
                ("P059999999Z", "Stationers", D(42_000))]  # no certificate: not listed
    c = wht_on_payments(payments, certs, date(2026, 1, 1), date(2026, 12, 31))
    # Mwangi: paid 300,000, certified 200,000 + 100,000 = 300,000 -> agrees
    # Audit Partners: paid 450,000, certified 500,000 -> -50,000 (tolerance 2,250) -> explain
    assert [(r.label, r.book, r.declared, r.variance) for r in c.rows] == [
        ("Mwangi Advocates (P051111111A)", D(300_000), D(300_000), D(0)),
        ("Audit Partners (P052222222B)", D(450_000), D(500_000), D(-50_000)),
    ]
    assert c.needs_explanation and c.notes == ("1 certificates dated outside the year ignored",)


def test_returns_are_checked():
    with pytest.raises(ValueError, match="appears twice"):
        read_vat(VAT + b"2026-01,1,0,0\n")
    with pytest.raises(ValueError, match="period must look like"):
        read_paye(b"period,gross_pay,paye\nJan 2026,1,1\n")
    with pytest.raises(ValueError, match="columns needed"):
        read_wht(b"pin,amount\nP1,1\n")
