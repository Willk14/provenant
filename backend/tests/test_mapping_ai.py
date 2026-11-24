"""The mapping agent (ADR 0022): Claude suggests trial balance mappings; nothing is used until a person confirms."""
import json

import pytest

from provenant.api import practice_ai
from tests.test_ai_harness import scripted_client, sse, tool
from tests.test_api import PREPARER, REVIEWER
from tests.test_tax_api import client_with_tb

URL = "/clients/{cid}/years/2026-12-31"


def submit(value, use_id="toolu_1"):
    return sse([tool("submit", value, use_id)])


@pytest.fixture
def claude(monkeypatch):
    def install(*responses):
        client, requests = scripted_client(*responses)
        monkeypatch.setattr(practice_ai, "client_factory", lambda: client)
        return requests

    yield install
    monkeypatch.setattr(practice_ai, "client_factory", None)


def test_claude_maps_what_no_rule_could_and_leaves_confirmed_accounts_alone(client, claude):
    cid = client_with_tb(client)
    base = URL.format(cid=cid)
    # A person has already confirmed cost of sales: Claude must not be asked about it.
    assert client.put(f"/clients/{cid}/mappings/5000", headers=REVIEWER,
                      json={"fs_line": "cost_of_sales", "tax_treatment": "deductible"}).status_code == 200
    requests = claude(
        submit({"mappings": [
            {"code": "5000", "fs_line": "cost_of_sales", "tax_treatment": "deductible", "reason": "Direct costs."},
            {"code": "5700", "fs_line": "operating_expenses", "tax_treatment": "balance_sheet", "reason": "Sundry."},
        ]}),
        submit({"mappings": [
            {"code": "5700", "fs_line": "operating_expenses", "tax_treatment": "deductible",
             "reason": "A small sundry expense incurred in running the business."},
        ]}, "toolu_2"),
    )
    got = client.post(f"{base}/mapping/claude", headers=PREPARER).json()
    assert got["suggested"] == 1 and got["sent"] == got["left"] + 1

    first = json.loads(requests[0].content)["messages"][0]["content"]
    assert "5700 | Sundry 7 | 5000.00 | none" in first and "5000 | Cost of sales" not in first
    retry = requests[1].content.decode()
    assert "was not in the list" in retry and "tax treatment balance_sheet does not apply" in retry

    accounts = {a["code"]: a for a in client.get(f"{base}/mapping", headers=PREPARER).json()["accounts"]}
    assert (accounts["5700"]["status"], accounts["5700"]["fs_line"], accounts["5700"]["tax_treatment"]) == (
        "suggested", "operating_expenses", "deductible")
    assert accounts["5700"]["rule"].startswith("claude: A small sundry expense")
    assert accounts["5000"]["status"] == "confirmed" and accounts["5000"]["confirmed_by"] == "rae@firm-a"

    todo = client.get("/todo", headers=REVIEWER).json()["items"]
    mapping = next(x for x in todo if x["kind"] == "mapping")
    assert mapping["by_claude"] is True and mapping["href"] == f"/clients/{cid}?tab=mapping&year=2026-12-31"


def test_the_mapping_agent_says_so_when_claude_is_off(client, monkeypatch):
    monkeypatch.setenv("PROVENANT_LEGAL_AGENT", "baseline")
    cid = client_with_tb(client)
    assert client.post(f"{URL.format(cid=cid)}/mapping/claude", headers=PREPARER).status_code == 409
