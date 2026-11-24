"""Matters, conflict checks, KYC, the file audit, the trust account and the AI trace, through the API."""
from datetime import date, timedelta
from types import SimpleNamespace

import provenant.worker as worker
from provenant.ai import record_call
from tests.test_api import OTHER_FIRM, PARTNER, PREPARER, REVIEWER, make_pdf, run_jobs, who
from tests.test_covenants_api import upload_agreement
from tests.test_legal_api import LINES, ScriptedAgent

SECOND_PARTNER = who("paz@firm-a", "partner")


def open_matter(client, title="Kilima acquisition of Baobab", parties=None, headers=PREPARER):
    parties = parties or [{"name": "Kilima Analytics Ltd", "role": "client", "identifier": "PVT-123"},
                          {"name": "Baobab Logistics Limited", "role": "counterparty"}]
    response = client.post("/matters", headers=headers, json={"title": title, "matter_type": "corporate",
                                                               "client_kind": "organisation", "parties": parties})
    assert response.status_code == 201, response.text
    return response.json()


def test_a_conflict_is_found_and_decided_by_someone_else(client):
    first = open_matter(client)
    assert first["reference"].startswith("M-") and first["status"] == "conflict_review"
    assert first["conflict"]["hits"] == []
    assert client.post(f"/matters/{first['id']}/conflict-decision", headers=PARTNER,
                       json={"decision": "cleared"}).json()["status"] == "open"

    # Now Baobab asks the firm to act against Kilima: Kilima is an existing client, so this is adverse.
    second = open_matter(client, "Baobab claim against Kilima", [
        {"name": "Baobab Logistics Ltd", "role": "client"}, {"name": "KILIMA ANALYTICS LIMITED", "role": "counterparty"}])
    kinds = [(h["kind"], h["source"]) for h in second["conflict"]["hits"]]
    assert ("adverse", first["reference"]) in kinds and second["possible_conflicts"] == 2
    url = f"/matters/{second['id']}/conflict-decision"
    assert client.post(url, headers=PREPARER, json={"decision": "conflict", "note": "Acting for both"}).status_code == 403
    assert client.post(url, headers=PARTNER, json={"decision": "cleared"}).status_code in (400, 422)  # needs reasons
    declined = client.post(url, headers=PARTNER, json={"decision": "conflict", "note": "We act for Kilima."}).json()
    assert declined["status"] == "declined"
    assert [e["action"] for e in declined["events"]] == ["opened", "conflict_check", "conflict_conflict"]
    assert client.get("/matters", headers=OTHER_FIRM).json() == []


def test_screening_list_hits_come_first(client):
    csv = b"name,identifier,reference\nKilima Analytics,PVT123,UN-0042\nSomeone Else,,UN-0043\n"
    assert client.post("/practice/watchlists", headers=PREPARER, data={"list_name": "UN consolidated"},
                       files={"file": ("un.csv", csv, "text/csv")}).status_code == 403
    assert client.post("/practice/watchlists", headers=PARTNER, data={"list_name": "UN consolidated"},
                       files={"file": ("un.csv", csv, "text/csv")}).json() == {"list_name": "UN consolidated", "entries": 2}
    hit = open_matter(client)["conflict"]["hits"][0]
    assert (hit["kind"], hit["source"], hit["reason"]) == ("screening", "UN consolidated (UN-0042)",
                                                           "same ID or registration number")


def test_kyc_items_are_verified_by_someone_else_and_the_audit_shows_gaps(client):
    matter = open_matter(client)
    client.post(f"/matters/{matter['id']}/conflict-decision", headers=PARTNER, json={"decision": "cleared"})
    gaps = {g["item"] for g in client.get(f"/matters/{matter['id']}", headers=PREPARER).json()["file_gaps"]}
    assert gaps == {"engagement_letter", "client_id", "beneficial_ownership"}  # an organisation, no money held yet
    letter = client.post(f"/matters/{matter['id']}/requirements", headers=PREPARER, data={"item": "engagement_letter"},
                         files={"file": ("letter.pdf", make_pdf(["Engagement letter"]), "application/pdf")}).json()
    assert client.post(f"/matters/{matter['id']}/requirements", headers=PREPARER, data={"item": "client_id"},
                       files={"file": ("id.pdf", b"MZ not a pdf", "application/pdf")}).status_code in (400, 422)
    verify = f"/matters/{matter['id']}/requirements/{letter['id']}/verify"
    assert client.post(verify, headers=PREPARER).status_code == 403
    assert client.post(verify, headers=REVIEWER).json()["status"] == "verified"
    audit = client.get("/practice/audit", headers=PREPARER).json()
    assert [{g["item"] for g in m["gaps"]} for m in audit["matters"]] == [{"client_id", "beneficial_ownership"}]


def test_advocates_need_a_current_practising_certificate(client):
    for headers in (PREPARER, REVIEWER, PARTNER):
        client.get("/me", headers=headers)
    client.post("/practice/certificates", headers=PARTNER, json={
        "holder": "pam@firm-a", "number": "LSK/2026/123", "expires_on": (date.today() + timedelta(days=200)).isoformat()})
    certificates = {g["label"]: g["problem"] for g in client.get("/practice/audit", headers=PREPARER).json()["certificates"]}
    assert certificates == {"Practising certificate: rae@firm-a": "missing"}  # Pam is current; Pat is not an advocate


def test_trust_reconciliation_is_prepared_then_approved_by_someone_else(client):
    matter = open_matter(client)
    account = client.post("/trust/accounts", headers=PARTNER, json={
        "name": "Client account", "bank": "Example Bank", "dormant_days": 365,
        "dormant_basis": "Firm policy: review balances untouched for a year"}).json()
    base = f"/trust/accounts/{account['id']}"
    cashbook = (f"date,amount,reference,description,matter\n"
                f"2026-11-03,500000.00,DEP1,Deposit from client,{matter['reference']}\n"
                f"2026-11-20,-150000.00,CHQ001,Stamp duty,{matter['reference']}\n"
                f"2026-11-29,75000.00,DEP2,Deposit in transit,{matter['reference']}\n").encode()
    assert client.post(f"{base}/entries/import", headers=PREPARER,
                       files={"file": ("cashbook.csv", cashbook, "text/csv")}).json() == {"imported": 3}
    statement = (b"date,description,amount,reference\n2026-11-03,Deposit,500000.00,DEP1\n"
                 b"2026-11-21,Cheque 001,-150000.00,CHQ001\n")
    prepared = client.post(f"{base}/reconciliations", headers=PREPARER, data={"as_of": "2026-11-30", "bank_balance": "350000.00"},
                           files={"statement": ("bank.csv", statement, "text/csv")}).json()
    paper = prepared["workpaper"]
    # Bank 350,000 + 75,000 in transit = 425,000 = cashbook = the one client ledger.
    assert (paper["adjusted_bank"], paper["cashbook"], paper["ledgers_total"]) == ("425000.00",) * 3
    assert prepared["balanced"] and [o["reference"] for o in paper["outstanding"]] == ["DEP2"]
    approve = f"{base}/reconciliations/{prepared['id']}/approve"
    assert client.post(approve, headers=PREPARER).status_code == 403
    assert client.post(approve, headers=REVIEWER).json()["approved_by"] == "rae@firm-a"


def test_a_break_cannot_be_approved(client):
    account = client.post("/trust/accounts", headers=PARTNER, json={"name": "Client account", "bank": "Example Bank"}).json()
    base = f"/trust/accounts/{account['id']}"
    client.post(f"{base}/entries", headers=PREPARER, json={"entry_date": "2026-11-05", "amount": "1000", "reference": "X"})
    prepared = client.post(f"{base}/reconciliations", headers=PREPARER, data={"as_of": "2026-11-30", "bank_balance": "1000"},
                           files={"statement": ("bank.csv", b"date,description,amount\n2026-11-05,Deposit,1000\n",
                                                "text/csv")}).json()
    assert not prepared["balanced"] and [a["kind"] for a in prepared["workpaper"]["alerts"]] == ["break", "unallocated"]
    assert client.post(f"{base}/reconciliations/{prepared['id']}/approve", headers=REVIEWER).status_code == 409
    assert client.get(f"{base}/entries", headers=OTHER_FIRM).status_code == 404


class TracedAgent(ScriptedAgent):
    def review(self, topics, toolbox):
        record_call("contract_review", "claude-opus-5-5", SimpleNamespace(stop_reason="end_turn"), 0.2,
                    trace=[("system", "You review contracts."), ("prompt", "Review this contract.")])
        super().review(topics, toolbox)


def test_every_claude_run_keeps_a_trace_that_only_a_partner_reads(client, app, monkeypatch):
    monkeypatch.setattr(worker, "agent_from_env", TracedAgent)
    doc_id = upload_agreement(client, app, LINES)
    client.post(f"/documents/{doc_id}/reviews", headers=PREPARER)
    run_jobs(app)
    run = client.get("/ai/runs", headers=PARTNER).json()["runs"][0]
    trace = client.get(f"/ai/runs/{run['id']}/trace", headers=PARTNER).json()
    assert [(t["kind"], t["content"]) for t in trace] == [("system", "You review contracts."),
                                                           ("prompt", "Review this contract.")]
    assert client.get(f"/ai/runs/{run['id']}/trace", headers=PREPARER).status_code == 403
    assert client.get(f"/ai/runs/{run['id']}/trace", headers=who("x@firm-b", "partner", "firm-b")).json() == []
