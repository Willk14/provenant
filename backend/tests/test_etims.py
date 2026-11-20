from decimal import Decimal

import pytest

from provenant.tax.etims import Decision, Status, read_etims, read_ledger, reconcile

D = Decimal

LEDGER = b"""date,supplier,supplier_pin,invoice_number,account_code,amount,etims_number,description
2026-02-03,Safari Stationers,P051111111A,INV-1,5200,"1,000.00",CU-001,paper
2026-02-10,Nairobi Cleaners,P052222222B,INV-77,5200,2500,,cleaning
2026-03-01,Rift Fuel,P053333333C,F-9,5200,5000,,fuel
2026-03-10,Kibo Catering,P054444444D,K-1,5200,3000,,staff lunch
2026-03-15,Jua Printers,P055555555E,J-5,5200,1200,,brochures
2026-03-31,Equity Bank,P056666666F,MAR-CHG,5200,800,,bank charges
"""

ETIMS = b"""cu_invoice_number,supplier_pin,supplier_name,invoice_number,date,taxable_value,vat
CU-001,P051111111A,Safari Stationers,INV-1,2026-02-03,1000.00,160.00
CU-002,P052222222B,Nairobi Cleaners,inv77,2026-02-10,2500,400
CU-003,P053333333C,Rift Fuel,F-9,2026-03-01,4600,736
CU-004,P054444444D,Kibo Catering,K-1A,2026-03-13,3000.50,480.08
CU-099,P057777777G,Unknown Supplier,X-1,2026-04-01,9999,1599.84
"""


def test_each_matching_rule_and_the_unsupported_total():
    lines, invoices = read_ledger(LEDGER), read_etims(ETIMS)
    exempt = {lines[5].key: Decision("exempt", "bank charges: a financial service, no eTIMS invoice", by="pat")}
    rec = reconcile(lines, invoices, exempt)
    by_supplier = {r.line.supplier: r for r in rec.lines}
    assert [(name, r.status) for name, r in by_supplier.items()] == [
        ("Safari Stationers", Status.MATCHED),  # control unit number recorded in the ledger
        ("Nairobi Cleaners", Status.MATCHED),  # PIN + invoice number ("INV-77" == "inv77" once normalised)
        ("Rift Fuel", Status.DIFFERS),  # ledger 5,000 vs eTIMS 4,600
        ("Kibo Catering", Status.PROBABLE),  # same PIN, 3,000 vs 3,000.50 (within 1.00), 3 days apart
        ("Jua Printers", Status.UNMATCHED),
        ("Equity Bank", Status.EXEMPT),
    ]
    assert by_supplier["Rift Fuel"].unsupported == D(400)
    assert "amount 5000 vs eTIMS 4600" in by_supplier["Rift Fuel"].note
    # Unsupported: Rift Fuel's excess 400 + Jua Printers 1,200 = 1,600 (probable lines need a decision first)
    assert rec.unsupported == D(1_600)
    assert [i.cu_invoice_number for i in rec.etims_only] == ["CU-099"]


def test_a_person_can_confirm_a_probable_match_or_name_one():
    lines, invoices = read_ledger(LEDGER), read_etims(ETIMS)
    kibo, jua = lines[3], lines[4]
    rec = reconcile(lines, invoices, {
        kibo.key: Decision("match", cu_invoice_number="CU-004", by="pat"),
        jua.key: Decision("match", cu_invoice_number="CU-099", by="pat"),  # wrong supplier: flagged, not hidden
    })
    results = {r.line.supplier: r for r in rec.lines}
    assert results["Kibo Catering"].status is Status.MATCHED and results["Kibo Catering"].decided_by == "pat"
    assert results["Jua Printers"].status is Status.DIFFERS
    assert "supplier PIN P055555555E vs eTIMS P057777777G" in results["Jua Printers"].note
    assert rec.etims_only == ()


def test_inputs_are_checked():
    with pytest.raises(ValueError, match="not a KRA PIN"):
        read_ledger(LEDGER.replace(b"P051111111A", b"12345"))
    with pytest.raises(ValueError, match="appears twice"):
        read_etims(ETIMS + b"CU-001,P051111111A,x,INV-9,2026-02-03,1,0\n")
    with pytest.raises(ValueError, match="columns needed"):
        read_ledger(b"date,amount\n2026-01-01,1\n")
    with pytest.raises(ValueError, match="say why"):
        Decision("exempt", "x")
