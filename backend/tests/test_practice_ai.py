"""Claude in the practice agents (ADR 0020): every answer is checked against its source before it is used."""
import pytest

from provenant.api import practice_ai
from tests.test_ai_harness import scripted_client, sse, tool
from tests.test_api import PARTNER, PREPARER
from tests.test_covenants_api import AGREEMENT_LINES, upload_agreement
from tests.test_phase2_api import opened
from tests.test_practice_api import open_matter

NOTICE = "HCCOMM No. E123 of 2026. TAKE NOTICE that judgment was delivered on 20th November 2026. Mention on 3/12/2026."


@pytest.fixture
def claude(monkeypatch):
    """Install scripted Claude replies; returns the list of requests the harness sent."""
    sent = []

    def install(*responses):
        client, requests = scripted_client(*responses)
        sent.append(requests)
        monkeypatch.setattr(practice_ai, "client_factory", lambda: client)
        return requests

    yield install
    monkeypatch.setattr(practice_ai, "client_factory", None)


def submit(value, use_id="toolu_1"):
    return sse([tool("submit", value, use_id)])


def test_claude_reads_a_notice_and_a_quote_not_in_the_notice_is_sent_back(client, claude):
    reading = {"case_number": "HCCOMM No. E123 of 2026", "event": "judgment",
               "event_quote": "judgment was delivered", "event_date": "2026-11-20",
               "event_date_quote": "delivered on 20th November 2026",
               "other_dates": [{"date": "2026-12-03", "quote": "Mention on 3/12/2026", "what": "mention"}]}
    requests = claude(submit({**reading, "event_quote": "judgment was entered"}),  # not in the notice
                      submit(reading, "toolu_2"))
    matter = opened(client)
    made = client.post(f"/matters/{matter['id']}/notices", headers=PREPARER, json={"text": NOTICE}).json()
    assert made["reading"]["reader"] == "claude" and made["reading"]["event"] == "judgment"
    assert made["reading"]["dates"] == ["2026-11-20", "2026-12-03"]  # the event's own date first
    assert made["reading"]["case_number"] == "HCCOMM NO. E123 OF 2026"
    assert len(requests) == 2 and b"event_quote is not in the notice" in requests[1].content
    usage = client.get("/ai/runs", headers=PARTNER).json()
    assert any(r["purpose"] == "notice_reading" for r in usage["runs"])


def test_the_rules_reader_takes_over_when_claude_is_off(client, monkeypatch):
    monkeypatch.setenv("PROVENANT_LEGAL_AGENT", "baseline")
    matter = opened(client)
    made = client.post(f"/matters/{matter['id']}/notices", headers=PREPARER, json={"text": NOTICE}).json()
    assert made["reading"]["reader"] == "rules" and made["reading"]["event"] == "judgment"


def test_claude_finds_a_match_spelling_misses_and_keeps_every_rules_match(client, claude):
    open_matter(client, parties=[{"name": "Kilima Analytics Ltd", "role": "client"}])
    # "KAL Holdings" is Kilima's parent; spelling alone scores it far below the threshold. Known name K0 is Kilima.
    claude(submit({"matches": [{"party": 7, "known": 0, "reason": "out of range"}]}),
           submit({"matches": [{"party": 1, "known": 0, "reason": "KAL is the initials of Kilima Analytics Ltd"}]},
                  "toolu_2"))
    second = open_matter(client, title="Mwangi v KAL Holdings",
                         parties=[{"name": "Mwangi Holdings", "role": "client"},
                                  {"name": "KAL Holdings", "role": "counterparty"}])
    hits = second["conflict"]["hits"]
    assert [(h["party"], h["matched"], h["found_by"], h["kind"]) for h in hits] == [
        ("KAL Holdings", "Kilima Analytics Ltd", "claude", "adverse")]
    assert hits[0]["reason"].startswith("Claude: KAL is the initials")


def test_reconciliation_explanations_use_real_items_and_the_difference_is_worked_out_here():
    outstanding = [{"id": "e1", "date": "2026-11-02", "amount": "50000.00", "reference": "INV-9", "matter": "M-1"}]
    unrecorded = [{"date": "2026-11-03", "amount": "49950.00", "reference": "INV9", "description": "RTGS less charge"}]
    practice_ai.client_factory = lambda: scripted_client(
        submit({"explanations": [{"entries": ["E1", "E9"], "bank_lines": ["B1"], "likely_cause": "x", "next_step": "y"}]}),
        submit({"explanations": [{"entries": ["E1"], "bank_lines": ["B1"], "likely_cause": "Bank charge of 50 deducted.",
                                  "next_step": "Post the charge to the office account."}]}, "toolu_2"))[0]
    try:
        got = practice_ai.explain_reconciliation(outstanding, unrecorded, [], None, "firm-a")
    finally:
        practice_ai.client_factory = None
    # Bank 49,950.00 less cashbook 50,000.00 = -50.00, from the stored amounts.
    assert got == [{"entries": outstanding, "bank_lines": unrecorded, "difference": "-50.00",
                    "likely_cause": "Bank charge of 50 deducted.", "next_step": "Post the charge to the office account."}]


def test_claude_suggests_precedent_values_only_with_a_quote_from_the_matter_documents(client, app, claude):
    precedent = client.post("/precedents", headers=PARTNER, json={
        "name": "Facility letter", "kind": "letter",
        "body": "To {{client_name}}. Liquidity floor: {{liquidity}}. First test: {{first_test}}."}).json()
    matter = opened(client)
    doc_id = upload_agreement(client, app, AGREEMENT_LINES)
    client.post(f"/matters/{matter['id']}/documents", headers=PREPARER, json={"document_ids": [doc_id]})
    claude(submit({"values": [{"field": "liquidity", "value": "KES 50,000,000", "document": 0, "page": 1,
                               "quote": "Liquidity shall never fall"}]}),  # not on the page
           submit({"values": [{"field": "liquidity", "value": "KES 50,000,000", "document": 0, "page": 1,
                               "quote": "Liquidity shall not at any time be less than KES 50,000,000"}]}, "toolu_2"))
    got = client.post(f"/matters/{matter['id']}/precedent-drafts/suggest", headers=PREPARER,
                      json={"precedent_id": precedent["id"], "values": {}}).json()
    assert got["empty"] == ["liquidity", "first_test"]  # client_name comes from the matter
    assert got["suggestions"] == [{"field": "liquidity", "value": "KES 50,000,000", "source": "facility.pdf, page 1",
                                   "quote": "Liquidity shall not at any time be less than KES 50,000,000"}]


def test_claude_drafts_the_kyc_request_naming_every_missing_item(client, claude):
    matter = opened(client)
    gaps = [g["label"] for g in client.get(f"/matters/{matter['id']}", headers=PREPARER).json()["file_gaps"]]
    assert gaps
    letter = "Dear Kilima Analytics Ltd,\n\nTo complete our client due diligence for this matter, please send us: " + \
             "; ".join(gaps) + ".\n\nYours faithfully,\n[[advocate name]]"
    claude(submit({"letter": "Dear client, please send us your documents. Thank you for your help with this."}),
           submit({"letter": letter}, "toolu_2"))
    got = client.post(f"/matters/{matter['id']}/kyc-request", headers=PREPARER).json()
    assert got["letter"] == letter.strip() and got["asks_for"] == gaps


def test_on_demand_claude_agents_say_so_when_claude_is_off(client, monkeypatch):
    monkeypatch.setenv("PROVENANT_LEGAL_AGENT", "baseline")
    matter = opened(client)
    assert client.post(f"/matters/{matter['id']}/kyc-request", headers=PREPARER).status_code == 409
