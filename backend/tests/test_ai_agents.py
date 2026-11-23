"""The AI agents page: an honest status, tenant-scoped work and no credential in the response."""
import pytest

from provenant.api import ai_agents
from tests.test_api import OTHER_FIRM, PREPARER
from tests.test_legal_drafting import FilledAgent, drafted


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in ("PROVENANT_LEGAL_AGENT", "PROVENANT_LEGAL_AGENT_ID", "PROVENANT_LEGAL_ENVIRONMENT_ID",
                 "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(ai_agents.Path, "home", lambda: ai_agents.Path("no-such-home"))


def test_status_says_when_claude_is_off_or_cannot_run(monkeypatch):
    assert ai_agents.engine_status().mode == "keyword"
    monkeypatch.setenv("PROVENANT_LEGAL_AGENT", "claude-api")
    assert ai_agents.engine_status().label == "Claude needs an API key"
    monkeypatch.setenv("PROVENANT_LEGAL_AGENT", "claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret-value")
    assert ai_agents.engine_status().mode == "needs_setup"  # managed agent ids are missing
    monkeypatch.setenv("PROVENANT_LEGAL_AGENT", "something-else")
    assert ai_agents.engine_status().label == "Unknown agent setting"


def test_status_shows_claude_on_and_never_returns_the_key(client, monkeypatch):
    monkeypatch.setenv("PROVENANT_LEGAL_AGENT", "claude-api")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret-value")
    response = client.get("/ai/agents", headers=PREPARER)
    assert response.status_code == 200 and response.json()["engine"]["mode"] == "claude"
    assert "sk-secret-value" not in response.text


def test_page_lists_agents_and_work_for_this_firm_only(client, app, monkeypatch):
    doc_id, _, draft = drafted(client, app, monkeypatch, FilledAgent)
    body = client.get("/ai/agents", headers=PREPARER).json()
    agents = {a["key"]: a for a in body["agents"]}
    assert (agents["drafter"]["runs"], agents["drafter"]["waiting"]) == (1, 1)
    assert [c["id"] for c in body["contracts"]] == [doc_id]
    assert body["activity"][0]["id"] == draft["id"] and body["activity"][0]["kind"] == "draft"

    other = client.get("/ai/agents", headers=OTHER_FIRM).json()
    assert other["contracts"] == [] and other["activity"] == [] and all(a["runs"] == 0 for a in other["agents"])


def test_managed_agents_setting_marks_drafting_unavailable(client, monkeypatch):
    monkeypatch.setenv("PROVENANT_LEGAL_AGENT", "claude")
    agents = {a["key"]: a for a in client.get("/ai/agents", headers=PREPARER).json()["agents"]}
    assert agents["drafter"]["available"] is False and agents["reviewer"]["available"] is True
