"""Precedent drafting, docketing and due diligence through the API."""
import io

from openpyxl import load_workbook

import provenant.worker as worker
from tests.test_api import OTHER_FIRM, PARTNER, PREPARER, REVIEWER, run_jobs, who
from tests.test_covenants_api import upload_agreement
from tests.test_legal_api import LINES, ScriptedAgent
from tests.test_practice_api import open_matter

SECOND_PARTNER = who("paz@firm-a", "partner")
LETTER = """ENGAGEMENT LETTER

To {{client_name}}, matter {{matter_reference}}.

We will act for you in {{matter_title}}. Our fee is {{fee}}.

[[optional:retainer]]A retainer of {{retainer}} is payable before work starts.[[/optional]]

Yours faithfully, {{responsible}}
"""


def opened(client):
    matter = open_matter(client)
    client.post(f"/matters/{matter['id']}/conflict-decision", headers=PARTNER, json={"decision": "cleared"})
    return matter


def test_only_a_partner_locks_precedents_and_versions_are_kept(client):
    body = {"name": "Engagement letter", "kind": "engagement_letter", "body": LETTER}
    assert client.post("/precedents", headers=PREPARER, json=body).status_code == 403
    made = client.post("/precedents", headers=PARTNER, json=body).json()
    assert made["version"] == 1 and made["fields"] == ["client_name", "matter_reference", "matter_title", "fee",
                                                       "retainer", "responsible"]
    assert made["options"] == ["retainer"]
    assert client.post(f"/precedents/{made['id']}/versions", headers=PARTNER,
                       json={"body": "Broken {{field"}).status_code in (400, 422)
    again = client.post(f"/precedents/{made['id']}/versions", headers=PARTNER, json={"body": LETTER + "\nP.S."}).json()
    assert again["version"] == 2 and client.get("/precedents", headers=OTHER_FIRM).json() == []


def test_a_draft_fills_from_the_matter_and_departures_need_a_partner(client):
    precedent = client.post("/precedents", headers=PARTNER, json={
        "name": "Engagement letter", "kind": "engagement_letter", "body": LETTER}).json()
    matter = opened(client)
    url = f"/matters/{matter['id']}/precedent-drafts"
    draft = client.post(url, headers=PREPARER, json={"precedent_id": precedent["id"], "values": {}}).json()
    assert "To Kilima Analytics Ltd, matter M-" in draft["text"] and draft["unfilled"] == ["fee"]
    assert "retainer" not in draft["text"].lower().split("fee")[1]  # optional clause left out
    decide = f"{url}/{draft['id']}/decision"
    assert client.post(decide, headers=REVIEWER, json={"decision": "approved"}).status_code == 409  # [[fee]] is open

    filled = draft["text"].replace("[[fee]]", "KES 250,000")
    edited = client.put(f"{url}/{draft['id']}", headers=REVIEWER, json={"text": filled}).json()
    assert [d["kind"] for d in edited["deviations"]] == ["changed"]  # filling a gap by hand is still a change
    assert client.post(decide, headers=REVIEWER, json={"decision": "approved"}).status_code == 403  # edited it
    assert client.post(decide, headers=who("ray@firm-a", "reviewer"), json={"decision": "approved"}).status_code == 403
    assert client.post(decide, headers=PARTNER, json={"decision": "approved"}).status_code in (400, 422)  # needs reasons
    approved = client.post(decide, headers=PARTNER, json={"decision": "approved", "note": "Fee agreed by email."}).json()
    assert approved["status"] == "approved"
    assert client.put(f"{url}/{draft['id']}", headers=PREPARER, json={"text": "changed"}).status_code == 409


def test_a_draft_with_every_field_supplied_matches_the_precedent(client):
    precedent = client.post("/precedents", headers=PARTNER, json={
        "name": "Engagement letter", "kind": "engagement_letter", "body": LETTER}).json()
    matter = opened(client)
    url = f"/matters/{matter['id']}/precedent-drafts"
    draft = client.post(url, headers=PREPARER, json={"precedent_id": precedent["id"], "include": ["retainer"],
                                                     "values": {"fee": "KES 250,000", "retainer": "KES 50,000"}}).json()
    assert draft["unfilled"] == [] and draft["deviations"] == []
    assert "A retainer of KES 50,000 is payable" in draft["text"]
    approved = client.post(f"{url}/{draft['id']}/decision", headers=REVIEWER, json={"decision": "approved"}).json()
    assert approved["status"] == "approved"  # no departures, so a reviewer may approve


def test_a_court_notice_becomes_deadlines_only_when_confirmed(client):
    assert client.post("/docket/rules", headers=PREPARER, json={
        "name": "Notice of appeal", "trigger": "judgment", "days": 14, "basis": "Firm practice note"}).status_code == 403
    client.post("/docket/rules", headers=PARTNER, json={
        "name": "Notice of appeal", "trigger": "judgment", "days": 14,
        "basis": "As entered by the firm from the applicable appellate rules"})
    client.post("/docket/rules", headers=PARTNER, json={
        "name": "Prepare for mention", "trigger": "mention", "days": 3, "basis": "Firm practice"})
    client.post("/docket/holidays", headers=PARTNER, json={"day": "2026-12-04", "name": "Example holiday"})
    matter = opened(client)
    notice = client.post(f"/matters/{matter['id']}/notices", headers=PREPARER, json={
        "text": "HCCOMM No. E123 of 2026. TAKE NOTICE that judgment was delivered on 20th November 2026."}).json()
    assert notice["reading"]["event"] == "judgment" and notice["reading"]["dates"] == ["2026-11-20"]
    assert client.get("/docket", headers=PREPARER).json() == []  # nothing docketed yet

    done = client.post(f"/matters/{matter['id']}/notices/{notice['id']}/confirm", headers=PREPARER, json={
        "event": "judgment", "event_date": "2026-11-20", "case_number": "HCCOMM E123/2026"}).json()
    # 14 days after Friday 20 November is Friday 4 December, a holiday here, so Monday 7 December.
    assert [(e["title"], e["due_on"]) for e in done["entries"]] == [("Notice of appeal", "2026-12-07")]
    assert "Basis: As entered by the firm" in done["entries"][0]["basis"]
    calendar = client.get("/docket.ics", headers=PREPARER)
    assert "DTSTART;VALUE=DATE:20261207" in calendar.text and "Notice of appeal" in calendar.text
    assert client.post(f"/matters/{matter['id']}/notices/{notice['id']}/confirm", headers=PREPARER, json={
        "event": "judgment", "event_date": "2026-11-20"}).status_code == 409
    entry = done["entries"][0]["id"]
    assert client.post(f"/docket/{entry}/done", headers=REVIEWER).json()["status"] == "done"
    assert client.get("/docket", headers=PREPARER).json() == [] and client.get("/docket", headers=OTHER_FIRM).json() == []


def test_due_diligence_reviews_every_contract_and_shows_one_matrix(client, app, monkeypatch):
    monkeypatch.setattr(worker, "agent_from_env", ScriptedAgent)
    matter = opened(client)
    doc_id = upload_agreement(client, app, LINES)
    base = f"/matters/{matter['id']}"
    assert client.post(f"{base}/documents", headers=PREPARER, json={"document_ids": [doc_id, doc_id]}).json() == {"added": 1}
    assert client.get(f"{base}/due-diligence", headers=PREPARER).json()["rows"][0]["cells"]["change_of_control"]["state"] == "not_reviewed"
    assert client.post(f"{base}/due-diligence/review", headers=PREPARER).json()["started"] == 1
    run_jobs(app)
    data = client.get(f"{base}/due-diligence", headers=PREPARER).json()
    cells = data["rows"][0]["cells"]
    assert (cells["change_of_control"]["severity"], cells["change_of_control"]["deviation"]) == ("high", True)
    assert cells["governing_law"]["state"] == "not_found" and data["reviewed"] == 1
    assert client.post(f"{base}/due-diligence/review", headers=PREPARER).json()["started"] == 0  # not paid for twice
    sheet = load_workbook(io.BytesIO(client.get(f"{base}/due-diligence.xlsx", headers=PREPARER).content)).active
    assert sheet["A3"].value.endswith(".pdf") and sheet["B3"].value == 2
    assert any("HIGH: Cancellation on change of control" in str(c.value) for c in sheet[3])
