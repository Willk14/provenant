"""eTIMS reconciliation through the API, into the tax computation."""
from decimal import Decimal

from tests.test_api import OTHER_FIRM, PREPARER
from tests.test_etims import ETIMS, LEDGER
from tests.test_tax_api import client_with_tb

D = Decimal
# Post Rift Fuel and Jua Printers to 5000 Cost of sales (deductible); the rest to 5200, which is not in this
# client's trial balance; and add a fine posted to 5300 KRA penalties (already disallowable).
CLIENT_LEDGER = (LEDGER.replace(b"F-9,5200", b"F-9,5000").replace(b"J-5,5200", b"J-5,5000")
                 + b"2026-04-02,Kanu County,P058888888H,FINE-1,5300,700,,parking fine\n")


def setup(client):
    cid = client_with_tb(client)
    client.post(f"/clients/{cid}/years/2026-12-31/confirm-suggestions", headers=PREPARER)
    client.put(f"/clients/{cid}/mappings/5700", headers=PREPARER,
               json={"fs_line": "operating_expenses", "tax_treatment": "deductible"})
    return cid, f"/clients/{cid}/years/2026-12-31"


def upload(client, url, data, name):
    return client.post(url, headers=PREPARER, files={"file": (name, data, "text/csv")})


def test_reconcile_decide_apply_and_trace_into_the_computation(client, app):
    cid, year = setup(client)
    before = client.get(f"{year}/tax", headers=PREPARER).json()
    # Baseline (no inputs): PBT 493,000 + penalties 10,000 + depreciation 80,000 + lease interest 12,000 = 595,000
    assert D(before["taxable_profit"]) == D(595_000) and D(before["tax_payable"]) == D(178_500)

    assert upload(client, f"{year}/etims/ledger", CLIENT_LEDGER, "purchases.csv").status_code == 201
    r = upload(client, f"{year}/etims/invoices", ETIMS, "etims.csv")
    assert r.status_code == 201, r.text
    rec = r.json()
    lines = {x["supplier"]: x for x in rec["lines"]}
    assert lines["Kanu County"]["status"] == "unmatched" and not lines["Kanu County"]["in_addback"]
    assert lines["Jua Printers"]["in_addback"] and not lines["Safari Stationers"]["in_addback"]  # 5200 not in TB
    # Add-back: Rift Fuel's excess 400 + Jua Printers 1,200 (both on 5000); the fine is already disallowable
    assert D(rec["totals"]["addback"]) == D(1_600) and rec["probable"] == 1

    r = client.post(f"{year}/etims/apply", headers=PREPARER, json={"apply": True})
    assert r.status_code == 409 and "probable" in r.json()["detail"]
    client.put(f"{year}/etims/decisions", headers=PREPARER, json={
        "line_key": lines["Kibo Catering"]["key"], "kind": "match", "cu_invoice_number": "CU-004"})
    applied = client.post(f"{year}/etims/apply", headers=PREPARER, json={"apply": True}).json()
    assert applied["applied_by"] == "pat@firm-a"

    c = client.get(f"{year}/tax", headers=PREPARER).json()
    line = next(a for a in c["adjustments"] if "eTIMS" in a["label"])
    assert D(line["amount"]) == D(1_600) and len(line["sources"]) == 2
    assert sorted(c["etims_lines"][s] for s in line["sources"]) == ["Jua Printers J-5", "Rift Fuel F-9"]
    assert D(c["taxable_profit"]) == D(596_600) and D(c["tax_payable"]) == D(178_980)  # 30% of 596,600

    # Exempting a purchase changes the total, so it must be applied again before it counts
    client.put(f"{year}/etims/decisions", headers=PREPARER, json={
        "line_key": lines["Jua Printers"]["key"], "kind": "exempt", "reason": "imported goods: customs entry held"})
    assert not any("eTIMS" in a["label"] for a in client.get(f"{year}/tax", headers=PREPARER).json()["adjustments"])
    client.post(f"{year}/etims/apply", headers=PREPARER, json={"apply": True})
    assert D(client.get(f"{year}/tax", headers=PREPARER).json()["taxable_profit"]) == D(595_400)


def test_etims_is_per_firm_and_checks_its_inputs(client, app):
    cid, year = setup(client)
    r = upload(client, f"{year}/etims/ledger", b"date,amount\n2026-01-01,1\n", "bad.csv")
    assert r.status_code == 422 and "columns needed" in r.json()["detail"]
    upload(client, f"{year}/etims/ledger", CLIENT_LEDGER, "purchases.csv")
    assert client.get(f"{year}/etims", headers=OTHER_FIRM).status_code == 404
    r = client.put(f"{year}/etims/decisions", headers=PREPARER, json={"line_key": "x", "kind": "exempt", "reason": "no"})
    assert r.status_code == 422 and "say why" in r.json()["detail"]
