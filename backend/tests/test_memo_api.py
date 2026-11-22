"""The board tax memo through the API: bound to the live computation, checks and approval."""
from tests.test_api import OTHER_FIRM, PREPARER
from tests.test_etims import ETIMS
from tests.test_etims_api import CLIENT_LEDGER, setup, upload
from tests.test_tax_api import client_with_tb


def text(block):
    return "".join(s["text"] for s in block["segments"])


def summary(m):
    return text(next(b for b in m["blocks"] if b["kind"] == "paragraph"))


def test_memo_follows_the_computation_and_flags_typed_amounts(client, app):
    _, year = setup(client)
    m = client.get(f"{year}/memo", headers=PREPARER).json()
    assert m["is_default"] and m["blocked"] is None
    # Baseline computation (see test_etims_api): taxable 595,000, tax 178,500, no credits
    assert "taxable profit is KES 595,000.00, and corporation tax at 30% is KES 178,500.00" in summary(m)
    checks = next(b for b in m["blocks"] if b["table"] == "checks")["rows"]
    assert checks[0] == ["eTIMS reconciliation", "Not uploaded", False]
    assert ["VAT turnover against revenue", "Not uploaded", False] in checks
    status = next(b for b in m["blocks"] if any(s["field"] == "approval_status" for s in b["segments"]))
    assert text(status) == "The computation has not been approved by a reviewer."

    # Apply an eTIMS add-back of 1,600: the memo's figures move with the computation
    upload(client, f"{year}/etims/ledger", CLIENT_LEDGER, "p.csv")
    rec = upload(client, f"{year}/etims/invoices", ETIMS, "e.csv").json()
    kibo = next(x for x in rec["lines"] if x["supplier"] == "Kibo Catering")
    client.put(f"{year}/etims/decisions", headers=PREPARER, json={"line_key": kibo["key"], "kind": "match",
                                                                 "cu_invoice_number": "CU-004"})
    client.post(f"{year}/etims/apply", headers=PREPARER, json={"apply": True})
    m = client.get(f"{year}/memo", headers=PREPARER).json()
    assert "taxable profit is KES 596,600.00, and corporation tax at 30% is KES 178,980.00" in summary(m)

    r = client.put(f"{year}/memo", headers=PREPARER, json={"body": "Tax is 178,980.00, i.e. {{tax_payable}}."})
    m = r.json()
    assert not m["is_default"] and m["updated_by"] == "pat@firm-a"
    assert summary(m) == "Tax is 178,980.00, i.e. KES 178,980.00."
    assert m["warnings"] == ["'178,980.00' is typed, not bound to the computation: insert a field instead"]
    bad = client.put(f"{year}/memo", headers=PREPARER, json={"body": "{{tax_owed}}"})
    assert bad.status_code == 422 and "tax_owed" in bad.json()["detail"]
    assert client.put(f"{year}/memo", headers=PREPARER, json={"body": ""}).json()["is_default"]
    assert client.get(f"{year}/memo", headers=OTHER_FIRM).status_code == 404


def test_memo_waits_for_the_mapping(client, app):
    cid = client_with_tb(client)  # suggestions not confirmed
    m = client.get(f"/clients/{cid}/years/2026-12-31/memo", headers=PREPARER).json()
    assert m["blocks"] == [] and "need a confirmed mapping" in m["blocked"]


def test_memo_reports_journal_entry_tests(client, app):
    from tests.test_api import REVIEWER
    from tests.test_journal import GL
    from tests.test_ledger_api import upload as upload_ledger

    _, year = setup(client)
    assert upload_ledger(client, year, GL.encode()).status_code == 201
    client.put(f"{year}/ledger/exceptions", headers=REVIEWER, json={
        "entry_id": "E5", "test": "unbalanced", "status": "cleared", "reason": "Rounding on a supplier credit note"})
    m = client.get(f"{year}/memo", headers=PREPARER).json()
    # No approval limit, so 8 tests run: 1 + 2 + 1 + 2 + 1 + 1 + 2 + 2 = 12 flagged items; E5's is cleared
    rows = next(b for b in m["blocks"] if b["table"] == "checks")["rows"]
    assert ["Journal entry tests", "12 flagged: 1 cleared, 0 escalated, 11 open", False] in rows
    compliance = next(b for b in m["blocks"] if any(s["field"] == "journal_entries" for s in b["segments"]))
    assert ("Journal entry tests (24 lines in 12 entries, tests je-2026.1) flagged 12 items: 1 cleared by a "
            "reviewer, 0 escalated, 11 open.") in text(compliance)
