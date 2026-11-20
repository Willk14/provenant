"""General ledger upload and journal entry tests through the API."""
from decimal import Decimal

from tests.test_api import OTHER_FIRM, PREPARER, REVIEWER, who
from tests.test_journal import GL
from tests.test_tax_api import client_with_tb

D = Decimal


def upload(client, year, data, headers=PREPARER, limit=""):
    return client.post(f"{year}/ledger", headers=headers, data={"approval_limit": limit},
                       files={"file": ("gl.csv", data, "text/csv")})


def test_upload_flags_reconciles_and_exceptions_follow_maker_checker(client, app):
    cid = client_with_tb(client)
    year = f"/clients/{cid}/years/2026-12-31"
    assert client.get(f"{year}/ledger", headers=PREPARER).json()["uploaded"] is False

    r = upload(client, year, GL.encode(), limit="10,000")
    assert r.status_code == 201, r.text
    out = r.json()
    assert (out["lines"], out["entries"], D(out["approval_limit"]), out["test_version"]) == (24, 12, D(10_000), "je-2026.1")
    counts = {t["key"]: t["count"] for t in out["tests"]}
    assert counts == {"unbalanced": 1, "duplicates": 2, "round_amounts": 1, "weekend": 2, "after_hours": 1,
                      "rare_users": 1, "rare_accounts": 2, "period_end": 2, "near_limit": 1}
    assert out["risk"][0]["entry_id"] == "E4" and len(out["risk"][0]["tests"]) == 4
    # Reconciliation, P&L accounts of the trial balance (see test_tax_api ROWS) against the year's ledger movement:
    # 4000: ledger -106,170 vs TB -1,000,000; 5300 KRA penalties: ledger 500,000 vs TB 10,000; 5000 not in the GL.
    recon = {x["account_code"]: (D(x["ledger"]), D(x["trial_balance"])) for x in out["reconciliation"]}
    assert recon["4000"] == (D(-106_170), D(-1_000_000)) and recon["5300"] == (D(500_000), D(10_000))
    assert recon["5000"] == (D(0), D(400_000))

    body = {"entry_id": "E5", "test": "unbalanced", "status": "cleared", "reason": "Rounding on a supplier credit note"}
    assert client.put(f"{year}/ledger/exceptions", headers=PREPARER, json=body).status_code == 403  # not a reviewer
    uploader_as_reviewer = who("pat@firm-a", "reviewer")
    assert client.put(f"{year}/ledger/exceptions", headers=uploader_as_reviewer, json=body).status_code == 403
    r = client.put(f"{year}/ledger/exceptions", headers=REVIEWER, json=body)
    assert r.status_code == 200, r.text
    unbalanced = next(t for t in r.json()["tests"] if t["key"] == "unbalanced")
    assert unbalanced["open"] == 0 and unbalanced["sample"][0]["decision"]["decided_by"] == "rae@firm-a"

    escalate = {"entry_id": "E4", "test": "after_hours", "status": "escalated", "reason": "Ask the client who temp1 is"}
    r = client.put(f"{year}/ledger/exceptions", headers=PREPARER, json=escalate).json()
    assert next(f for f in next(t for t in r["tests"] if t["key"] == "after_hours")["sample"]
                if f["entry_id"] == "E4")["decision"]["status"] == "escalated"
    short = {**escalate, "reason": "why"}
    assert client.put(f"{year}/ledger/exceptions", headers=PREPARER, json=short).status_code == 422

    lines = client.get(f"{year}/ledger/entries/E5", headers=PREPARER).json()
    assert [(x["account_code"], D(x["amount"])) for x in lines] == [("5200", D(1000)), ("1000", D(-900))]
    assert client.get(f"{year}/ledger", headers=OTHER_FIRM).status_code == 404

    no_limit = client.put(f"{year}/ledger/settings", headers=PREPARER, json={"approval_limit": None}).json()
    assert next(t for t in no_limit["tests"] if t["key"] == "near_limit")["applies"] is False


def test_bad_ledgers_are_refused(client, app):
    cid = client_with_tb(client)
    year = f"/clients/{cid}/years/2026-12-31"
    r = upload(client, year, b"entry_id,date,account_code,amount\nA,2026-02-30,1000,1\n")
    assert r.status_code == 422 and "row 2" in r.json()["detail"]
    assert upload(client, year, GL.encode(), limit="-5").status_code == 422
