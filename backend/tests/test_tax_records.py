"""Tax records a consultant inputs: fixed asset register, credits, supporting documents, prior-year roll forward."""
from decimal import Decimal

from tests.test_api import OTHER_FIRM, PREPARER, make_pdf
from tests.test_tax_api import client_with_tb

D = Decimal
YEAR = "/clients/{cid}/years/2026-12-31"

REGISTER = b"""asset_code,description,class,acquired,cost,disposed,proceeds
PM-01,CNC router,Plant and machinery,2026-03-01,"100,000.00",,
PM-02,Old press,Plant and machinery,2020-01-15,50000,2026-06-30,20000
MV-01,Delivery van,Motor vehicles,2026-05-01,80000,,
PM-00,Scrapped drill,Plant and machinery,2018-01-01,9000,2024-02-01,0
"""

INPUTS = {
    "losses_brought_forward": "50,000",
    "allowances": [{"name": "Plant and machinery", "wdv_brought_forward": "300,000", "additions": "999",
                    "rate_percent": "25", "method": "reducing", "basis": "Second Schedule (as entered by preparer)",
                    "asset_opening_values": {"PM-02": "300,000"}}],
}


def ready_client(client):
    cid = client_with_tb(client)
    client.post(f"{YEAR.format(cid=cid)}/confirm-suggestions", headers=PREPARER)
    client.put(f"/clients/{cid}/mappings/5700", headers=PREPARER,
               json={"fs_line": "operating_expenses", "tax_treatment": "deductible"})
    assert client.put(f"{YEAR.format(cid=cid)}/inputs", headers=PREPARER, json=INPUTS).status_code == 200
    return cid


def upload(client, cid, path, data, name, **form):
    return client.post(f"{YEAR.format(cid=cid)}/{path}", headers=PREPARER, data=form,
                       files={"file": (name, data, "application/octet-stream")})


def test_the_register_supplies_additions_and_disposals(client, app):
    cid = ready_client(client)
    r = upload(client, cid, "fixed-assets", REGISTER, "far.csv")
    assert r.status_code == 201, r.text
    classes = {c["asset_class"]: c for c in r.json()["classes"]}
    plant = classes["Plant and machinery"]
    assert (D(plant["additions"]), D(plant["disposals"]), plant["assets_added"], plant["assets_disposed"]) == (
        D(100_000), D(20_000), ["PM-01"], ["PM-02"])
    assert plant["assets_held"] == 1 and D(classes["Motor vehicles"]["additions"]) == D(80_000)

    c = client.get(f"{YEAR.format(cid=cid)}/tax", headers=PREPARER).json()
    (allowance,) = c["allowances"]
    # New asset: 25% x 100,000 = 25,000. Existing item: 25% x 300,000 = 75,000;
    # disposal loss = 20,000 proceeds - (300,000 - 75,000 residual) = -205,000.
    assert allowance["from_register"] and D(allowance["allowance"]) == D(100_000)
    assert D(c["taxable_profit"]) == D(240_000) and D(c["tax_payable"]) == D(72_000)
    loss = next(x for x in c["adjustments"] if x["label"].startswith("Disposal loss"))
    assert D(loss["amount"]) == D(-205_000) and loss["sources"] == ["asset:PM-02"]
    assert any("Motor vehicles" in w and "no capital deduction class" in w for w in c["warnings"])

    # Uploading again replaces the register
    r = upload(client, cid, "fixed-assets", REGISTER.replace(b"80000", b"90000"), "far-v2.csv")
    assert r.json()["filename"] == "far-v2.csv" and len(r.json()["assets"]) == 4
    assert client.get(f"{YEAR.format(cid=cid)}/fixed-assets", headers=PREPARER).json()["filename"] == "far-v2.csv"


def test_register_errors_are_specific(client, app):
    cid = ready_client(client)
    late = REGISTER + b"PM-09,Next year's lathe,Plant and machinery,2027-01-05,1000,,\n"
    r = upload(client, cid, "fixed-assets", late, "far.csv")
    assert r.status_code == 422 and "after the year end" in r.json()["detail"]
    r = upload(client, cid, "fixed-assets", REGISTER.replace(b"2026-03-01", b"01/03/2026"), "far.csv")
    assert r.status_code == 422 and "row 2" in r.json()["detail"]
    missing_proceeds = b"""asset_code,description,class,acquired,cost,disposed,proceeds
PM-02,Old press,Plant and machinery,2020-01-15,50000,2026-06-30,
"""
    r = upload(client, cid, "fixed-assets", missing_proceeds, "far.csv")
    assert r.status_code == 422 and "proceeds are required when an asset is disposed" in r.json()["detail"]
    r = upload(client, cid, "fixed-assets", b"code,cost\nA,1\n", "far.csv")
    assert r.status_code == 422 and "columns needed" in r.json()["detail"]
    assert client.get(f"{YEAR.format(cid=cid)}/fixed-assets", headers=PREPARER).json() is None


def test_business_cessation_requires_a_complete_disposed_register(client, app):
    cid = ready_client(client)
    ceased = {**INPUTS, "allowances": [{**INPUTS["allowances"][0], "business_ceased": True}]}
    assert client.put(f"{YEAR.format(cid=cid)}/inputs", headers=PREPARER, json=ceased).status_code == 200
    blocked = client.get(f"{YEAR.format(cid=cid)}/tax", headers=PREPARER)
    assert blocked.status_code == 409 and "business-cessation" in blocked.json()["detail"]

    held_assets = upload(client, cid, "fixed-assets", REGISTER, "far.csv")
    assert held_assets.status_code == 201
    blocked = client.get(f"{YEAR.format(cid=cid)}/tax", headers=PREPARER)
    assert blocked.status_code == 409 and "cease being owned by year end" in blocked.json()["detail"]


def test_cessation_balancing_deduction_is_traced_and_awaits_review(client, app):
    cid = ready_client(client)
    disposed_register = b"""asset_code,description,class,acquired,cost,disposed,proceeds
PM-02,Old press,Plant and machinery,2020-01-15,50000,2026-06-30,20000
"""
    assert upload(client, cid, "fixed-assets", disposed_register, "cessation-register.csv").status_code == 201
    ceased = {**INPUTS, "allowances": [{**INPUTS["allowances"][0], "business_ceased": True}]}
    assert client.put(f"{YEAR.format(cid=cid)}/inputs", headers=PREPARER, json=ceased).status_code == 200

    result = client.get(f"{YEAR.format(cid=cid)}/tax", headers=PREPARER)
    assert result.status_code == 200, result.text
    computation = result.json()
    (allowance,) = computation["allowances"]
    # Opening residual value 300,000 less 75,000 current deduction = 225,000;
    # 225,000 residual less 20,000 proceeds = 205,000 balancing deduction.
    assert allowance["business_ceased"] is True
    assert D(allowance["balancing_deduction"]) == D(205_000)
    deduction = next(x for x in computation["adjustments"] if x["label"].startswith("Balancing deduction on cessation"))
    assert D(deduction["amount"]) == D(-205_000) and deduction["sources"] == ["asset:PM-02"]
    assert "paragraph 4" in deduction["basis"]
    assert not any(x["label"].startswith("Disposal loss") for x in computation["adjustments"])
    assert computation["approval"] is None


def test_credits_with_evidence_give_tax_due(client, app):
    cid = ready_client(client)
    cert = upload(client, cid, "attachments", make_pdf(["Withholding tax certificate", "Amount 15,000"]),
                  "wht-safari-bank.pdf", category="withholding_certificate", account_code="4000", note="Q3")
    assert cert.status_code == 201, cert.text
    cert_id = cert.json()["id"]
    inputs = {**INPUTS, "credits": [
        {"kind": "instalment", "description": "Instalments paid", "amount": "40,000", "reference": "PRN 2026-04",
         "paid_on": "2026-04-20"},
        {"kind": "withholding", "description": "Safari Bank", "amount": "15000", "evidence": cert_id},
    ], "prior_return": {"reference": "KRA-ACK-2025-001", "filed_on": "2026-06-28"}}
    assert client.put(f"{YEAR.format(cid=cid)}/inputs", headers=PREPARER, json=inputs).status_code == 200
    saved = client.get(f"{YEAR.format(cid=cid)}/inputs", headers=PREPARER).json()
    assert saved["prior_return"] == {"reference": "KRA-ACK-2025-001", "filed_on": "2026-06-28",
                                    "assessed_tax": "", "agriculture_income_ratio": "0"}

    c = client.get(f"{YEAR.format(cid=cid)}/tax", headers=PREPARER).json()
    # No register here, so the typed additions count: 25% x 300,999 = 75,249.75 allowance,
    # taxable 469,750.25, tax 140,925.08 (rounded); less credits 40,000 and 15,000
    assert D(c["tax_payable"]) == D("140925.08") and D(c["tax_due"]) == D("85925.08")
    assert c["credit_items"][1]["evidence_filename"] == "wht-safari-bank.pdf"
    assert [x["sources"] for x in c["credits"]] == [["credit:0"], ["credit:1"]]
    assert D(c["instalment_plan"]["installments"][0]["paid_to_date"]) == D("31481.27")
    assert D(c["instalment_plan"]["installments"][1]["paid_to_date"]) == D("8518.73")

    # Evidence cannot be deleted while a credit relies on it
    assert client.delete(f"/clients/{cid}/attachments/{cert_id}", headers=PREPARER).status_code == 409
    f = client.get(f"/clients/{cid}/attachments/{cert_id}/file", headers=PREPARER)
    assert f.status_code == 200 and f.content.startswith(b"%PDF") and f.headers["x-content-type-options"] == "nosniff"
    assert client.get(f"/clients/{cid}/attachments/{cert_id}/file", headers=OTHER_FIRM).status_code == 404


def test_attachment_checks(client, app):
    cid = ready_client(client)
    r = upload(client, cid, "attachments", b"MZ\x90\x00", "tool.exe", category="other")
    assert r.status_code == 422 and "PDF, PNG" in r.json()["detail"]
    r = upload(client, cid, "attachments", b"<html>not a pdf", "statement.pdf", category="bank_statement")
    assert r.status_code == 422 and "not a PDF" in r.json()["detail"]
    r = upload(client, cid, "attachments", b"date,amount\n", "bank.csv", category="receipts")
    assert r.status_code == 422 and "category" in r.json()["detail"]
    ok = upload(client, cid, "attachments", b"date,amount\n2026-01-02,100\n", "bank.csv", category="bank_statement")
    assert ok.status_code == 201
    listed = client.get(f"{YEAR.format(cid=cid)}/attachments", headers=PREPARER).json()
    assert [a["filename"] for a in listed] == ["bank.csv"]
    assert client.delete(f"/clients/{cid}/attachments/{ok.json()['id']}", headers=PREPARER).status_code == 204

    other = client.post("/clients", headers=PREPARER, json={"name": "Other client Ltd"}).json()["id"]
    foreign = client.post(f"/clients/{other}/years/2026-12-31/attachments", headers=PREPARER,
                          data={"category": "other"}, files={"file": ("x.csv", b"a\n", "text/csv")}).json()["id"]
    bad = {**INPUTS, "credits": [{"kind": "withholding", "description": "x", "amount": "1", "evidence": foreign}]}
    r = client.put(f"{YEAR.format(cid=cid)}/inputs", headers=PREPARER, json=bad)
    assert r.status_code == 422 and "not this client's" in r.json()["detail"]


def test_roll_forward_proposes_the_opening_position(client, app):
    cid = ready_client(client)
    upload(client, cid, "fixed-assets", REGISTER, "far.csv")
    overpaid = {**INPUTS, "credits": [{"kind": "instalment", "description": "Instalments", "amount": "87,000"}]}
    client.put(f"{YEAR.format(cid=cid)}/inputs", headers=PREPARER, json=overpaid)  # tax 72,000: 15,000 refundable

    assert client.post(f"{YEAR.format(cid=cid)}/inputs/roll-forward", headers=PREPARER).status_code == 409  # no FY2025
    client_with_tb(client, year_end="2027-12-31")
    r = client.post(f"/clients/{cid}/years/2027-12-31/inputs/roll-forward", headers=PREPARER)
    assert r.status_code == 200, r.text
    proposal = r.json()
    assert D(proposal["losses_brought_forward"]) == 0  # all 50,000 used in 2026
    (plant,) = proposal["allowances"]
    assert (D(plant["wdv_brought_forward"]), plant["rate_percent"], plant["additions"]) == (D(75_000), "25", "0")
    assert plant["asset_opening_values"] == {"PM-01": "75000.00"}
    assert proposal["credits"][0]["kind"] == "overpayment" and D(proposal["credits"][0]["amount"]) == D(15_000)
    # A proposal only: nothing saved until a person saves it
    assert client.get(f"/clients/{cid}/years/2027-12-31/inputs", headers=PREPARER).json()["allowances"] == []
