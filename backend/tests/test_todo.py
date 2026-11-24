"""To review: one list of what waits for a person, and who may act on it (ADR 0021)."""
from datetime import date, timedelta

from tests.test_api import OTHER_FIRM, PARTNER, PREPARER, REVIEWER
from tests.test_phase2_api import opened
from tests.test_practice_api import open_matter


def kinds(client, headers):
    return [(x["kind"], x["yours"]) for x in client.get("/todo", headers=headers).json()["items"]]


def test_nobody_is_asked_to_decide_what_they_prepared(client):
    matter = open_matter(client)  # opened by the preparer
    mine = client.get("/todo", headers=PREPARER).json()
    conflict = next(x for x in mine["items"] if x["kind"] == "conflict")
    assert conflict["yours"] is False and conflict["why_not"] == "you opened it"
    assert conflict["href"] == f"/matters/{matter['id']}" and conflict["prepared_by"] == "pat@firm-a"
    theirs = next(x for x in client.get("/todo", headers=REVIEWER).json()["items"] if x["kind"] == "conflict")
    assert theirs["yours"] is True
    assert client.get("/todo", headers=OTHER_FIRM).json()["items"] == []  # another firm sees nothing


def test_overdue_deadlines_come_first_then_what_you_can_act_on(client):
    matter = opened(client)
    base = f"/matters/{matter['id']}"
    later = (date.today() + timedelta(days=10)).isoformat()
    late = (date.today() - timedelta(days=1)).isoformat()
    far = (date.today() + timedelta(days=60)).isoformat()  # outside the 14 days: not listed
    for title, due in (("Written submissions", later), ("Notice of appeal", late), ("Costs hearing", far)):
        assert client.post(f"{base}/docket", headers=PREPARER, json={"title": title, "due_on": due,
                                                                      "basis": "Entered by hand"}).status_code == 201
    client.post(f"{base}/notices", headers=PREPARER, json={"text": "TAKE NOTICE that judgment was delivered on 2nd November 2026."})
    items = client.get("/todo", headers=REVIEWER).json()["items"]
    titles = [x["title"] for x in items if x["kind"] == "deadline"]
    assert titles == ["Notice of appeal", "Written submissions"]
    assert items[0]["title"] == "Notice of appeal" and items[0]["urgent"] is True
    notice = next(x for x in items if x["kind"] == "notice")
    assert notice["prepared_by"] == "rules reader" and notice["by_claude"] is False
    assert any(x["kind"] == "kyc" for x in items)  # the open matter has KYC gaps


def test_counts_split_what_is_yours_from_what_waits_on_others(client):
    open_matter(client)
    body = client.get("/todo", headers=PARTNER).json()
    assert body["yours"] == sum(1 for x in body["items"] if x["yours"]) and body["yours"] >= 1
