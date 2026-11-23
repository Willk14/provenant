"""The paralegal drafting assistant: grounded figures, placeholders and sign-off."""
import json

import pytest

import provenant.worker as worker
from provenant.ai import capture
from provenant.extraction.pdftext import PageText
from provenant.legal import ContractDocument
from provenant.legal.agent import ApiReviewAgent, BaselineAgent, ManagedReviewAgent
from provenant.legal.drafting import DraftToolbox, content_hash, facts
from tests.test_ai_harness import scripted_client, sse, tool
from tests.test_api import OTHER_FIRM, PARTNER, PREPARER, REVIEWER, run_jobs, who
from tests.test_covenants_api import upload_agreement
from tests.test_legal_api import LINES

TEXT = "The Borrower shall repay within thirty (30) days.\nThe Facility is KES 50,000,000 at 13.5% per annum."
QUOTE = {"document": "D1", "page": 1, "quote": "KES 50,000,000 at 13.5% per annum"}
DAYS = {"document": "D1", "page": 1, "quote": "within thirty (30) days"}


def page() -> PageText:
    chars = tuple({"x0": i, "x1": i + 1, "top": 10, "bottom": 20} if c != "\n" else None for i, c in enumerate(TEXT))
    return PageText(1, TEXT, chars)


def box(instructions="Ask the borrower to repay.") -> DraftToolbox:
    return DraftToolbox([ContractDocument("doc-1", "facility.pdf", "credit_agreement", "a" * 64, (page(),))], instructions)


def write(tools, text, citations=(), title="Notice of repayment"):
    return tools.call("write_draft", {"title": title, "paragraphs": [{"text": text, "citations": list(citations)}],
                                      "open_points": ["Check the notice clause."]})


def test_facts_normalise_separators_number_words_and_currencies():
    # 50,000,000 loses its commas; "thirty" is 30; Ksh and KES are one currency; $ is USD.
    assert facts("Ksh 50,000,000 in thirty days") == {"50000000", "30", "KES"}
    assert facts("USD 2.50 or $3") == {"2.50", "3", "USD"}
    assert facts("one of the parties") == set()  # "one" is too common in ordinary prose to treat as a figure


def test_a_figure_must_come_from_the_cited_words_or_the_instructions():
    tools = box()
    assert "50000000" in write(tools, "Pay KES 50,000,000.")  # right figure, but nothing cited
    assert "60000000" in write(tools, "Pay KES 60,000,000.", [QUOTE])  # a changed amount
    assert "USD" in write(tools, "Pay USD 50,000,000.", [QUOTE])  # a changed currency
    assert "14.5" in write(tools, "Interest is 14.5%.", [QUOTE])
    assert "60" in write(tools, "Repay within sixty days.", [DAYS])  # a changed number word
    assert write(tools, "Pay KES 50,000,000 at 13.5%.", [{**QUOTE, "quote": "KES 60,000,000 at 13.5% per annum"}]).startswith("ERROR")
    assert tools.draft is None
    assert write(tools, "Repay KES 50,000,000 within thirty (30) days.", [QUOTE, DAYS]) == "Draft recorded for a lawyer to review."
    assert tools.finished and len(tools.draft.paragraphs[0].citations) == 2


def test_the_instructions_can_supply_a_figure_and_placeholders_hold_the_rest():
    assert write(box("Give notice dated 1 December 2026."), "Dated 1 December 2026.") == "Draft recorded for a lawyer to review."
    tools = box()
    assert write(tools, "Pay by [[payment date 2027]] to [[account]].") == "Draft recorded for a lawyer to review."
    assert "malformed" in write(box(), "Pay by [[payment date.")


def test_the_draft_tools_cannot_record_findings():
    tools = box()
    assert tools.call("record_finding", {}).startswith("ERROR: unknown tool")
    assert tools.blocked_calls == 1


def test_baseline_writes_a_skeleton_with_no_legal_wording_of_its_own():
    tools = box("The repayment deadline for the borrower")
    BaselineAgent().draft("notice", "The repayment deadline for the borrower", tools)
    texts = [x.text for x in tools.draft.paragraphs]
    assert tools.draft.title == "Notice (skeleton)" and any("[[substance of this notice]]" in t for t in texts)
    assert any(x.citations for x in tools.draft.paragraphs)


def test_managed_agent_says_it_cannot_draft():
    with pytest.raises(RuntimeError, match="claude-api"):
        ManagedReviewAgent(None, "agent", "env").draft("notice", "Draft a notice.", box())


def test_claude_corrects_an_ungrounded_figure_and_its_usage_is_recorded():
    bad = {"title": "Notice", "paragraphs": [{"text": "Pay KES 99,000,000.", "citations": []}], "open_points": []}
    good = {"title": "Notice", "paragraphs": [{"text": "Pay KES 50,000,000.", "citations": [QUOTE]}],
            "open_points": ["Check service."]}
    client, requests = scripted_client(sse([tool("write_draft", bad)]), sse([tool("write_draft", good, "toolu_2")]))
    tools = box()
    with capture() as runs:
        ApiReviewAgent(client).draft("notice", "Demand repayment.", tools)
    first, second = (json.loads(r.content) for r in requests)
    assert "write_draft" in {t["name"] for t in first["tools"]} and "record_finding" not in {t["name"] for t in first["tools"]}
    assert "<instructions>" in first["messages"][0]["content"] and "paralegal" in first["system"]
    assert second["messages"][2]["content"][0]["is_error"] is True
    assert tools.draft.paragraphs[0].text == "Pay KES 50,000,000." and runs[0].purpose == "contract_draft"


class FilledAgent(BaselineAgent):
    name = "scripted-drafter"

    def draft(self, kind, instructions, toolbox):
        toolbox.call("write_draft", {"title": "Notice of cancellation", "paragraphs": [
            {"text": "The Lender may cancel the facility on a Change of Control.",
             "citations": [{"document": "D1", "page": 1, "quote": "the Lender may cancel the facility"}]},
            {"text": "Signed: [[signatory]]", "citations": []}], "open_points": ["Check how notice must be served."]})


def drafted(client, app, monkeypatch, agent=FilledAgent, headers=REVIEWER):
    monkeypatch.setattr(worker, "agent_from_env", agent)
    doc_id = upload_agreement(client, app, LINES)
    response = client.post(f"/documents/{doc_id}/drafts", headers=headers, json={
        "kind": "notice", "instructions": "Cancel the facility on a change of control."})
    assert response.status_code == 202 and response.json()["status"] == "queued"
    assert client.post(f"/documents/{doc_id}/drafts", headers=headers, json={
        "kind": "notice", "instructions": "A second draft at once."}).status_code == 409
    run_jobs(app)
    url = f"/documents/{doc_id}/drafts/{response.json()['id']}"
    return doc_id, url, client.get(f"/documents/{doc_id}/drafts", headers=headers).json()[0]


def test_draft_is_edited_then_approved_by_someone_else_and_locked(client, app, monkeypatch):
    doc_id, url, draft = drafted(client, app, monkeypatch)
    assert draft["status"] == "ready" and draft["paragraphs"][0]["citations"][0]["page"] == 1
    assert client.get(f"/documents/{doc_id}/drafts", headers=OTHER_FIRM).status_code == 404

    second_partner = who("paz@firm-a", "partner")
    assert client.post(f"{url}/decision", headers=PARTNER, json={"decision": "approved"}).status_code == 409  # placeholder
    assert client.post(f"{url}/decision", headers=PREPARER, json={"decision": "approved"}).status_code == 403
    assert client.post(f"{url}/decision", headers=REVIEWER, json={"decision": "approved"}).status_code == 403  # requested it
    assert client.post(f"{url}/decision", headers=PARTNER, json={"decision": "returned"}).status_code in (400, 422)
    returned = client.post(f"{url}/decision", headers=PARTNER, json={"decision": "returned", "note": "Name the signatory."})
    assert returned.json()["status"] == "returned"

    texts = [draft["paragraphs"][0]["text"], "Signed: A. Director"]
    edited = client.put(url, headers=PARTNER, json={"title": draft["title"], "paragraphs": texts}).json()
    assert edited["status"] == "ready" and edited["version"] == 2
    assert edited["content_sha256"] == content_hash(draft["title"], texts)
    assert [p["edited"] for p in edited["paragraphs"]] == [False, True]
    assert client.post(f"{url}/decision", headers=PARTNER, json={"decision": "approved"}).status_code == 403  # edited it

    approved = client.post(f"{url}/decision", headers=second_partner, json={"decision": "approved"}).json()
    assert approved["status"] == "approved"
    assert [e["action"] for e in approved["history"]] == ["generated", "returned", "edited", "approved"]
    assert client.put(url, headers=PARTNER, json={"title": "Changed", "paragraphs": texts}).status_code == 409


def test_a_failed_draft_is_recorded_and_not_paid_for_again(client, app, monkeypatch):
    class Failing(FilledAgent):
        calls = 0

        def draft(self, kind, instructions, toolbox):
            type(self).calls += 1
            raise RuntimeError("no draft")

    _, _, draft = drafted(client, app, monkeypatch, Failing)
    assert draft["status"] == "failed" and "no draft" in draft["error"] and Failing.calls == 1


def test_short_instructions_are_refused(client, app):
    doc_id = upload_agreement(client, app, LINES)
    assert client.post(f"/documents/{doc_id}/drafts", headers=PREPARER, json={
        "kind": "letter", "instructions": "short"}).status_code in (400, 422)
