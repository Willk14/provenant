import provenant.worker as worker
from provenant.legal import DEFAULT_TOPICS
from tests.test_api import OTHER_FIRM, PARTNER, PREPARER, REVIEWER, review, run_jobs, who
from tests.test_covenants_api import AGREEMENT_LINES, upload_agreement

LINES = [
    *AGREEMENT_LINES,
    "On a Change of Control the Lender may cancel the facility and demand repayment.",
    "EBITDA add-backs are capped at 20% of EBITDA.",
]
OTHERS = [t.key for t in DEFAULT_TOPICS if t.key not in ("change_of_control", "ebitda_definition")]
SECOND_PARTNER = who("paz@firm-a", "partner")


class ScriptedAgent:
    name = "scripted"

    def review(self, topics, toolbox):
        common = dict(document="D1", page=1, suggestion="Ask for notice only.", proposed_term=None, proposed_value=None)
        toolbox.call("record_finding", {**common, "topic": "change_of_control", "severity": "high",
                                        "title": "Cancellation on change of control",
                                        "explanation": "The lender may cancel, which goes beyond notice only.",
                                        "quote": "the Lender may cancel the facility"})
        toolbox.call("record_finding", {**common, "topic": "ebitda_definition", "severity": "medium",
                                        "title": "Capped add-backs", "explanation": "Add-backs are capped at 20%.",
                                        "quote": "capped at 20% of EBITDA", "proposed_term": "addback_cap_percent",
                                        "proposed_value": "20%"})
        toolbox.call("finish_review", {"summary": "Two points for the lawyer.", "not_found": OTHERS})

    def ask(self, question, topics, toolbox):
        toolbox.call("answer_question", {"answer": "The lender may cancel.", "not_stated": False, "citations": [
            {"document": "D1", "page": 1, "quote": "the Lender may cancel the facility"}]})


class StoppingAgent(ScriptedAgent):
    def review(self, topics, toolbox):
        toolbox.call("list_documents", {})


def reviewed(client, app, monkeypatch, agent=None):
    monkeypatch.setattr(worker, "agent_from_env", lambda: agent or ScriptedAgent())
    doc_id = upload_agreement(client, app, LINES)
    started = client.post(f"/documents/{doc_id}/reviews", headers=PREPARER)
    assert started.status_code == 202 and started.json()["status"] == "queued"
    assert client.post(f"/documents/{doc_id}/reviews", headers=PREPARER).status_code == 409  # one at a time
    run_jobs(app)
    url = f"/documents/{doc_id}/reviews/{started.json()['id']}"
    return doc_id, url, client.get(url, headers=PREPARER).json()


def test_review_returns_cited_findings_ranked_by_severity(client, app, monkeypatch):
    doc_id, url, body = reviewed(client, app, monkeypatch)
    assert body["status"] == "done" and body["agent"] == "scripted" and body["not_found"] == OTHERS
    assert [f["severity"] for f in body["findings"]] == ["high", "medium"]
    first = body["findings"][0]
    assert first["citation"]["quote"] == "the Lender may cancel the facility" and first["citation"]["page"] == 1
    assert first["citation"]["bbox"][2] > first["citation"]["bbox"][0]  # located on the printed page
    assert first["decision"] is None
    assert client.get(url, headers=OTHER_FIRM).status_code == 404
    assert len(client.get(f"/documents/{doc_id}/reviews", headers=PREPARER).json()) == 1


def test_accepting_a_finding_only_puts_its_term_up_for_review(client, app, monkeypatch):
    doc_id, url, body = reviewed(client, app, monkeypatch)
    finding = next(f for f in body["findings"] if f["proposed_term"])
    decide = f"{url}/findings/{finding['id']}"
    assert client.put(decide, headers=PREPARER, json={"decision": "accepted"}).status_code == 403  # uploaded it
    assert client.put(decide, headers=OTHER_FIRM, json={"decision": "accepted"}).status_code == 404
    accepted = client.put(decide, headers=REVIEWER, json={"decision": "accepted"})
    assert accepted.status_code == 200 and accepted.json()["field_id"]
    assert client.put(decide, headers=PARTNER, json={"decision": "rejected", "note": "Changed my mind"}).status_code == 409

    fields = {f["name"]: f for f in client.get(f"/documents/{doc_id}", headers=PREPARER).json()["fields"]}
    term = fields["addback_cap_percent"]
    assert (term["value"], term["status"], term["extractor"]) == ("20%", "needs_review", "legal-agent")
    assert term["entered_by"] == "rae@firm-a" and term["page"] == 1
    assert review(client, term["id"], REVIEWER).status_code == 403  # the person who accepted it cannot verify it
    assert review(client, term["id"], PARTNER).status_code == 200


def test_rejection_needs_a_reason_and_creates_no_term(client, app, monkeypatch):
    doc_id, url, body = reviewed(client, app, monkeypatch)
    finding = next(f for f in body["findings"] if f["proposed_term"])
    decide = f"{url}/findings/{finding['id']}"
    assert client.put(decide, headers=REVIEWER, json={"decision": "rejected"}).status_code == 422
    assert client.put(decide, headers=REVIEWER, json={"decision": "rejected", "note": "Not a cap"}).status_code == 200
    assert "addback_cap_percent" not in {f["name"] for f in client.get(f"/documents/{doc_id}", headers=PREPARER).json()["fields"]}


def test_a_finding_never_overwrites_an_existing_term(client, app, monkeypatch):
    doc_id, url, body = reviewed(client, app, monkeypatch)
    entered = client.post(f"/documents/{doc_id}/inputs", headers=PREPARER, json={
        "name": "addback_cap_percent", "value": "15%", "basis": "Facility agreement as amended"})
    assert entered.status_code == 201
    finding = next(f for f in body["findings"] if f["proposed_term"])
    accepted = client.put(f"{url}/findings/{finding['id']}", headers=REVIEWER, json={"decision": "accepted"})
    assert accepted.status_code == 200 and accepted.json()["field_id"] is None
    fields = {f["name"]: f for f in client.get(f"/documents/{doc_id}", headers=PREPARER).json()["fields"]}
    assert fields["addback_cap_percent"]["value"] == "15%"


def test_an_agent_that_stops_early_is_a_failed_review_and_is_not_retried(client, app, monkeypatch):
    _, _, body = reviewed(client, app, monkeypatch, StoppingAgent())
    assert body["status"] == "failed" and "accounting for every topic" in body["error"] and body["findings"] == []


def test_questions_are_answered_with_citations(client, app, monkeypatch):
    _, url, _ = reviewed(client, app, monkeypatch)
    assert client.post(f"{url}/questions", headers=PREPARER, json={"question": "?"}).status_code == 422
    assert client.post(f"{url}/questions", headers=OTHER_FIRM, json={"question": "Can they cancel?"}).status_code == 404
    assert client.post(f"{url}/questions", headers=PREPARER, json={"question": "Can they cancel?"}).status_code == 202
    run_jobs(app)
    question = client.get(url, headers=PREPARER).json()["questions"][0]
    assert question["status"] == "done" and question["citations"][0]["quote"] == "the Lender may cancel the facility"


def test_only_a_partner_changes_the_firm_playbook_and_past_reviews_keep_theirs(client, app, monkeypatch):
    doc_id, url, before = reviewed(client, app, monkeypatch)
    new = {"positions": {"indemnity": "Our clients give no indemnities at all."}}
    assert client.put("/legal/playbook", headers=REVIEWER, json=new).status_code == 403
    saved = client.put("/legal/playbook", headers=PARTNER, json=new)
    assert saved.status_code == 200
    topic = next(t for t in saved.json()["topics"] if t["key"] == "indemnity")
    assert topic["position"].startswith("Our clients") and topic["standard"] is False
    assert client.put("/legal/playbook", headers=PARTNER, json={"positions": {"nope": "A position of length."}}).status_code == 422
    assert client.get("/legal/playbook", headers=OTHER_FIRM).json()["topics"][3]["standard"] is True  # another firm
    assert client.get(url, headers=PREPARER).json()["playbook_sha256"] == before["playbook_sha256"]
    again = client.post(f"/documents/{doc_id}/reviews", headers=PREPARER).json()
    assert again["playbook_sha256"] == saved.json()["sha256"] != before["playbook_sha256"]
