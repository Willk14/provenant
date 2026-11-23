"""The Claude contract chat: checked answers, follow-ups and private conversations."""
import json

import provenant.worker as worker
from provenant.ai import capture
from provenant.legal import ContractDocument
from provenant.legal.agent import ApiReviewAgent, BaselineAgent, chat_prompt
from provenant.legal.chat import ChatToolbox
from tests.test_ai_harness import scripted_client, sse, tool
from tests.test_api import OTHER_FIRM, PARTNER, PREPARER, run_jobs
from tests.test_covenants_api import upload_agreement
from tests.test_legal_api import LINES
from tests.test_legal_drafting import QUOTE, page


def box(question="What is the facility?") -> ChatToolbox:
    return ChatToolbox([ContractDocument("doc-1", "facility.pdf", "credit_agreement", "a" * 64, (page(),))], question)


def answer(tools, text, citations=(), not_stated=False):
    return tools.call("answer_question", {"answer": text, "citations": list(citations), "not_stated": not_stated})


def test_an_answer_cannot_state_a_figure_it_did_not_cite():
    tools = box()
    assert "60000000" in answer(tools, "It is KES 60,000,000.", [QUOTE])
    assert "USD" in answer(tools, "It is USD 50,000,000.", [QUOTE])
    assert answer(tools, "It is KES 50,000,000.", [{**QUOTE, "quote": "KES 90,000,000 at 13.5% per annum"}]).startswith("ERROR")
    assert tools.answer is None
    assert answer(tools, "It is KES 50,000,000 at 13.5%.", [QUOTE]) == "Answer recorded."


def test_a_silent_contract_is_said_plainly_and_cannot_carry_a_figure():
    assert "30" in answer(box(), "Probably 30 days.", not_stated=True)
    tools = box()
    assert answer(tools, "The documents do not say.", not_stated=True) == "Answer recorded."
    assert tools.answer.not_stated and tools.answer.citations == ()


def test_a_figure_in_the_question_may_be_repeated():
    tools = box("Is it above 40,000,000?")
    assert answer(tools, "Yes, KES 50,000,000 is above 40,000,000.", [QUOTE]) == "Answer recorded."


def test_chat_has_no_tool_that_changes_the_review():
    tools = box()
    assert tools.call("record_finding", {}).startswith("ERROR: unknown tool")
    assert tools.call("write_draft", {}).startswith("ERROR: unknown tool")


def test_earlier_turns_are_marked_as_conversation():
    prompt = chat_prompt("And the rate?", [("user", "What is the facility?"), ("assistant", "KES 50,000,000.")])
    assert prompt.startswith("<conversation>\nuser: What is the facility?") and "<question>\nAnd the rate?" in prompt


def test_claude_corrects_an_uncited_figure_and_usage_is_recorded():
    bad = {"answer": "It is KES 99,000,000.", "citations": [], "not_stated": False}
    good = {"answer": "It is KES 50,000,000.", "citations": [QUOTE], "not_stated": False}
    client, requests = scripted_client(sse([tool("answer_question", bad)]), sse([tool("answer_question", good, "toolu_2")]))
    tools = box()
    with capture() as runs:
        ApiReviewAgent(client).chat("What is the facility?", [], tools)
    first, second = (json.loads(r.content) for r in requests)
    assert {t["name"] for t in first["tools"]} == {"list_documents", "read_page", "search", "find_relevant_clauses",
                                                   "answer_question"}
    assert second["messages"][2]["content"][0]["is_error"] is True
    assert tools.answer.text == "It is KES 50,000,000." and runs[0].purpose == "contract_chat"


class CitingAgent(BaselineAgent):
    name = "scripted-chat"
    seen = None

    def chat(self, question, history, toolbox):
        type(self).seen = history
        toolbox.call("answer_question", {"answer": "The Lender may cancel.", "not_stated": False, "citations": [
            {"document": "D1", "page": 1, "quote": "the Lender may cancel the facility"}]})


def test_a_conversation_continues_and_stays_private(client, app, monkeypatch):
    monkeypatch.setattr(worker, "agent_from_env", CitingAgent)
    doc_id = upload_agreement(client, app, LINES)
    started = client.post(f"/documents/{doc_id}/chats", headers=PREPARER, json={"message": "Can they cancel?"})
    assert started.status_code == 202 and started.json()["status"] == "queued"
    url = f"/documents/{doc_id}/chats/{started.json()['id']}/messages"
    assert client.post(url, headers=PREPARER, json={"message": "Too soon"}).status_code == 409
    run_jobs(app)

    chat = client.get(f"/documents/{doc_id}/chats", headers=PREPARER).json()[0]
    assert chat["status"] == "idle" and [m["role"] for m in chat["messages"]] == ["user", "assistant"]
    assert chat["messages"][1]["citations"][0]["quote"] == "the Lender may cancel the facility"

    assert client.post(url, headers=PREPARER, json={"message": "On what event?"}).status_code == 202
    run_jobs(app)
    assert [role for role, _ in CitingAgent.seen] == ["user", "assistant"]  # the follow-up sees the earlier turn
    assert len(client.get(f"/documents/{doc_id}/chats", headers=PREPARER).json()[0]["messages"]) == 4

    assert client.get(f"/documents/{doc_id}/chats", headers=PARTNER).json() == []  # another person at the firm
    assert client.post(url, headers=PARTNER, json={"message": "Let me in"}).status_code == 404
    assert client.get(f"/documents/{doc_id}/chats", headers=OTHER_FIRM).status_code == 404


def test_a_failed_turn_is_recorded_and_the_person_can_ask_again(client, app, monkeypatch):
    class Failing(BaselineAgent):
        def chat(self, question, history, toolbox):
            raise RuntimeError("no answer")

    monkeypatch.setattr(worker, "agent_from_env", Failing)
    doc_id = upload_agreement(client, app, LINES)
    started = client.post(f"/documents/{doc_id}/chats", headers=PREPARER, json={"message": "Can they cancel?"}).json()
    run_jobs(app)
    chat = client.get(f"/documents/{doc_id}/chats", headers=PREPARER).json()[0]
    assert chat["status"] == "failed" and "no answer" in chat["error"] and len(chat["messages"]) == 1

    monkeypatch.setattr(worker, "agent_from_env", CitingAgent)
    assert client.post(f"/documents/{doc_id}/chats/{started['id']}/messages", headers=PREPARER,
                       json={"message": "Try again"}).status_code == 202
    run_jobs(app)
    assert client.get(f"/documents/{doc_id}/chats", headers=PREPARER).json()[0]["status"] == "idle"


def test_free_mode_returns_the_closest_wording(client, app, monkeypatch):
    monkeypatch.setattr(worker, "agent_from_env", BaselineAgent)
    doc_id = upload_agreement(client, app, LINES)
    client.post(f"/documents/{doc_id}/chats", headers=PREPARER, json={"message": "What about EBITDA add-backs?"})
    run_jobs(app)
    reply = client.get(f"/documents/{doc_id}/chats", headers=PREPARER).json()[0]["messages"][1]
    assert reply["text"].startswith("Keyword scan only") and reply["citations"]
