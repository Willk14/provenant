"""The Claude harness, through the real SDK on a mocked transport: no network and no cost."""
import itertools
import json
from types import SimpleNamespace

import anthropic
import httpx2 as httpx
import pytest

import provenant.worker as worker
from provenant.ai import BudgetExceeded, DeadlineExceeded, Limits, Refused, capture, cost_microusd, record_call, run_tools
from provenant.api import ai_runs
from provenant.legal import DEFAULT_TOPICS
from provenant.legal.agent import ApiReviewAgent, agent_from_env
from tests.test_api import OTHER_FIRM, PARTNER, PREPARER, run_jobs
from tests.test_covenants_api import upload_agreement
from tests.test_legal_api import LINES, ScriptedAgent
from tests.test_legal_toolbox import ALL_OTHERS, FINDING, box

USAGE = {"input_tokens": 1000, "output_tokens": 500, "cache_read_input_tokens": 2000, "cache_creation_input_tokens": 400}


def sse(blocks, stop_reason="tool_use", model="claude-opus-5-5"):
    """A Messages API event stream holding the given (kind, payload) content blocks."""
    events = [("message_start", {"type": "message_start", "message": {
        "id": "msg_1", "type": "message", "role": "assistant", "model": model, "content": [], "stop_reason": None,
        "stop_sequence": None, "usage": {**USAGE, "output_tokens": 1}}})]
    for index, (kind, payload) in enumerate(blocks):
        if kind == "thinking":
            start = {"type": "thinking", "thinking": "", "signature": ""}
            delta = {"type": "signature_delta", "signature": payload}
        else:
            start = {"type": "tool_use", "id": payload["id"], "name": payload["name"], "input": {}}
            delta = {"type": "input_json_delta", "partial_json": json.dumps(payload["input"])}
        events += [("content_block_start", {"type": "content_block_start", "index": index, "content_block": start}),
                   ("content_block_delta", {"type": "content_block_delta", "index": index, "delta": delta}),
                   ("content_block_stop", {"type": "content_block_stop", "index": index})]
    events += [("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                                  "usage": {"output_tokens": USAGE["output_tokens"]}}),
               ("message_stop", {"type": "message_stop"})]
    body = "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events)
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)


def tool(name, tool_input, use_id="toolu_1"):
    return ("tool", {"id": use_id, "name": name, "input": tool_input})


def scripted_client(*responses):
    requests, queue = [], list(responses)

    def handler(request):
        requests.append(request)
        return queue.pop(0)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    return anthropic.Anthropic(api_key="test", http_client=http, max_retries=0), requests


def plain_run(client, *, execute=lambda name, tool_input: "ok", done=lambda: False, **kwargs):
    return run_tools(client, purpose="test", system="System.", prompt="Do it.", tools=[{
        "name": "work", "description": "Work.", "input_schema": {"type": "object", "properties": {}}}],
        execute=execute, done=done, sleep=lambda _: None, **kwargs)


def test_cost_is_worked_out_from_each_kind_of_token():
    # Opus 5.5, in USD per million tokens: input 4, output 20, cache read 0.20, cache write 5.
    # 1,000 x 4 = 4,000; 500 x 20 = 10,000; 2,000 x 0.20 = 400; 400 x 5 = 2,000. Total 16,400 micro-dollars.
    assert cost_microusd("claude-opus-5-5", 1000, 500, 2000, 400) == 16_400
    # Rounding: 3 cache reads x 0.20 = 0.6, which rounds to 1 micro-dollar.
    assert cost_microusd("claude-opus-5-5", 0, 0, 3, 0) == 1
    assert cost_microusd("a-model-with-no-price", 1000, 500) is None


def test_review_runs_on_the_messages_api_and_records_usage_without_content():
    client, requests = scripted_client(
        sse([("thinking", "signed-thinking"), tool("record_finding", FINDING)]),
        sse([tool("finish_review", {"summary": "A short summary.", "not_found": ALL_OTHERS}, "toolu_2")]),
    )
    tools = box()
    with capture() as runs:
        ApiReviewAgent(client).review(DEFAULT_TOPICS, tools)
    assert tools.finished and len(tools.findings) == 1

    first, second = (json.loads(r.content) for r in requests)  # no third, paid turn once the review is finished
    assert len(requests) == 2 and "server-side-fallback-2026-07-01" in requests[0].headers["anthropic-beta"]
    assert first["model"] == "claude-opus-5-5" and first["thinking"] == {"type": "adaptive"}
    assert first["fallbacks"] == "default" and first["cache_control"] == {"type": "ephemeral"}
    assert first["stream"] is True and {t["name"] for t in first["tools"]} >= {"read_page", "finish_review"}
    assert second["messages"][1]["content"][0]["signature"] == "signed-thinking"  # thinking is passed back unchanged
    assert second["messages"][2]["content"] == [{"type": "tool_result", "tool_use_id": "toolu_1",
                                                 "content": second["messages"][2]["content"][0]["content"]}]

    (run,) = runs
    assert (run.purpose, run.status, run.turns, run.tool_calls) == ("contract_review", "completed", 2, 2)
    assert (run.input_tokens, run.output_tokens, run.cache_read_tokens, run.cache_write_tokens) == (2000, 1000, 4000, 800)
    assert run.cost_microusd == 32_800  # two turns of 16,400
    assert not {"prompt", "content", "quote", "text"} & set(vars(run))


def test_a_refused_tool_call_is_sent_back_as_an_error():
    client, requests = scripted_client(
        sse([tool("read_page", {"document": "D1", "page": 99})]),
        sse([tool("finish_review", {"summary": "A short summary.", "not_found": [t.key for t in DEFAULT_TOPICS]})]),
    )
    ApiReviewAgent(client).review(DEFAULT_TOPICS, box())
    result = json.loads(requests[1].content)["messages"][2]["content"][0]
    assert result["is_error"] is True and result["content"].startswith("ERROR: page must be")


@pytest.mark.parametrize(("stop_reason", "error", "status"), [
    ("refusal", Refused, "refused"), ("max_tokens", RuntimeError, "failed"), ("end_turn", RuntimeError, "failed")])
def test_a_tool_call_is_not_run_when_the_model_did_not_finish_it(stop_reason, error, status):
    client, _ = scripted_client(sse([tool("work", {})], stop_reason=stop_reason))
    ran = []
    with capture() as runs, pytest.raises(error):
        plain_run(client, execute=lambda *a: ran.append(a) or "ok")
    assert ran == [] and runs[0].status == status and runs[0].stop_reason == stop_reason


def test_the_deadline_stops_a_stream_and_is_recorded():
    client, _ = scripted_client(sse([tool("work", {})]))
    ticks = itertools.count(0, 10)  # ten seconds pass at every clock read, against a 25-second limit
    with capture() as runs, pytest.raises(DeadlineExceeded):
        plain_run(client, limits=Limits(deadline_seconds=25), clock=lambda: next(ticks))
    assert runs[0].status == "deadline" and runs[0].duration_ms > 0


def test_the_spending_limit_stops_the_next_paid_turn():
    client, requests = scripted_client(sse([tool("work", {})]), sse([tool("work", {})]))
    with capture() as runs, pytest.raises(BudgetExceeded):
        plain_run(client, limits=Limits(max_cost_microusd=16_400))  # one turn costs exactly this
    assert len(requests) == 1 and runs[0].status == "budget"


def test_an_unpriced_fallback_model_still_counts_against_the_spending_limit():
    client, requests = scripted_client(sse([tool("work", {})], model="claude-unlisted"))
    with capture() as runs, pytest.raises(BudgetExceeded):
        plain_run(client, limits=Limits(max_cost_microusd=1000))
    assert len(requests) == 1 and runs[0].cost_microusd is None and runs[0].models == ["claude-unlisted"]


def test_overloaded_is_retried_and_a_bad_request_is_not():
    overloaded = httpx.Response(529, headers={"retry-after": "0"}, json={
        "type": "error", "error": {"type": "overloaded_error", "message": "busy"}})
    client, requests = scripted_client(overloaded, sse([tool("work", {})]))
    done = iter([True])
    assert plain_run(client, done=lambda: next(done)).status == "completed" and len(requests) == 2

    bad = httpx.Response(400, json={"type": "error", "error": {"type": "invalid_request_error", "message": "no"}})
    client, requests = scripted_client(bad)
    with pytest.raises(anthropic.BadRequestError):
        plain_run(client)
    assert len(requests) == 1


def test_claude_api_reader_is_chosen_from_the_environment(monkeypatch):
    monkeypatch.setenv("PROVENANT_LEGAL_AGENT", "claude-api")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    assert isinstance(agent_from_env(), ApiReviewAgent)


class MeteredAgent(ScriptedAgent):
    def review(self, topics, toolbox):
        usage = SimpleNamespace(**USAGE)
        record_call("contract_review", "claude-opus-5-5", SimpleNamespace(
            model="claude-opus-5-5", stop_reason="end_turn", usage=usage), 1.5)
        super().review(topics, toolbox)


def test_worker_stores_runs_for_the_tenant_and_only_a_partner_reads_them(client, app, monkeypatch):
    monkeypatch.setattr(worker, "agent_from_env", MeteredAgent)
    doc_id = upload_agreement(client, app, LINES)
    assert client.post(f"/documents/{doc_id}/reviews", headers=PREPARER).status_code == 202
    run_jobs(app)

    response = client.get("/ai/runs", headers=PARTNER)
    body = response.json()
    assert response.status_code == 200 and body["total_runs"] == 1 and body["estimated_cost_microusd"] == 16_400
    assert body["runs"][0]["document_id"] == doc_id and body["runs"][0]["duration_ms"] == 1500
    assert "Lender" not in response.text  # usage only, never contract text
    assert client.get("/ai/runs", headers=PREPARER).status_code == 403
    assert client.get("/ai/runs", headers=OTHER_FIRM).json()["total_runs"] == 0


def test_a_ledger_failure_does_not_fail_work_already_paid_for():
    def broken_factory():
        raise RuntimeError("database is down")

    with ai_runs.recording(broken_factory, "firm-a", None):
        record_call("extraction_lease", "claude-opus-5-5", SimpleNamespace(stop_reason="end_turn"), 0.1)
