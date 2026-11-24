"""Work notifications and documents received by email."""
from email.message import EmailMessage

import pytest

import provenant.worker as worker
from provenant.api import inbox, notifications
from provenant.api.db import NotificationRow, make_engine, make_sessionmaker, tenant_session
from tests.test_api import OTHER_FIRM, PARTNER, PREPARER, REVIEWER, make_pdf, run_jobs, who
from tests.test_covenants_api import upload_agreement
from tests.test_legal_api import LINES, ScriptedAgent
from tests.test_legal_drafting import FilledAgent

SECOND_PARTNER = who("paz@firm-a", "partner")


@pytest.fixture
def sent(monkeypatch):
    outbox = []
    monkeypatch.setenv("PROVENANT_SMTP_HOST", "smtp.firm.test")
    monkeypatch.setenv("PROVENANT_SMTP_FROM", "provenant@firm.test")
    monkeypatch.setenv("PROVENANT_APP_URL", "https://app.provenant.test")
    monkeypatch.setattr(notifications, "send_email", lambda to, subject, body: outbox.append((to, subject, body)))
    return outbox


def sign_in(client, *people):
    for headers in people:
        assert client.get("/me", headers=headers).status_code == 200


def test_findings_email_reviewers_and_partners_but_not_the_uploader(client, app, monkeypatch, sent):
    sign_in(client, PREPARER, REVIEWER, PARTNER, OTHER_FIRM)
    monkeypatch.setattr(worker, "agent_from_env", ScriptedAgent)
    doc_id = upload_agreement(client, app, LINES)  # uploaded by the preparer
    assert client.post(f"/documents/{doc_id}/reviews", headers=PREPARER).status_code == 202
    run_jobs(app)
    assert sorted(to for to, _, _ in sent) == ["pam@firm-a", "rae@firm-a"]  # not the uploader, not another firm
    _, subject, body = sent[0]
    assert subject == "Contract review findings are waiting for you"
    assert f"https://app.provenant.test/documents/{doc_id}?tab=review" in body
    for private in ("Lender", "cancel", ".pdf", "EBITDA"):  # no contract content leaves by email
        assert private not in subject + body


def test_draft_emails_follow_the_sign_off(client, app, monkeypatch, sent):
    sign_in(client, PREPARER, REVIEWER, PARTNER, SECOND_PARTNER)
    monkeypatch.setattr(worker, "agent_from_env", FilledAgent)
    doc_id = upload_agreement(client, app, LINES)
    draft = client.post(f"/documents/{doc_id}/drafts", headers=REVIEWER, json={
        "kind": "notice", "instructions": "Cancel the facility on a change of control."}).json()
    run_jobs(app)
    assert sorted(to for to, s, _ in sent if s == "A draft is waiting for review") == ["pam@firm-a", "paz@firm-a"]
    url = f"/documents/{doc_id}/drafts/{draft['id']}"
    client.post(f"{url}/decision", headers=PARTNER, json={"decision": "returned", "note": "Name the signatory."})
    run_jobs(app)
    assert [to for to, s, _ in sent if s.startswith("A draft was returned")] == ["rae@firm-a"]


def test_turning_notifications_off_is_respected(client, app, monkeypatch, sent):
    sign_in(client, PREPARER, REVIEWER, PARTNER)
    assert client.put("/me/notifications", headers=REVIEWER, json={"notify": False}).status_code == 200
    monkeypatch.setattr(worker, "agent_from_env", ScriptedAgent)
    doc_id = upload_agreement(client, app, LINES)
    client.post(f"/documents/{doc_id}/reviews", headers=PREPARER)
    run_jobs(app)
    assert [to for to, _, _ in sent] == ["pam@firm-a"]


def test_without_an_smtp_server_emails_are_marked_skipped(client, app, monkeypatch, database_url):
    monkeypatch.delenv("PROVENANT_SMTP_HOST", raising=False)
    sign_in(client, PREPARER, PARTNER)
    monkeypatch.setattr(worker, "agent_from_env", ScriptedAgent)
    doc_id = upload_agreement(client, app, LINES)
    client.post(f"/documents/{doc_id}/reviews", headers=PREPARER)
    run_jobs(app)
    with tenant_session(make_sessionmaker(make_engine(database_url)), "firm-a") as s:
        assert [r.status for r in s.query(NotificationRow)] == ["skipped"]


@pytest.fixture
def mailroom(monkeypatch):
    monkeypatch.setenv("PROVENANT_INBOUND_SECRET", "inbound-secret")
    monkeypatch.setenv("PROVENANT_INBOUND_DOMAIN", "in.provenant.test")
    monkeypatch.setenv("PROVENANT_INBOUND_TOKEN", "hook-token")
    return inbox.inbound_address("firm-a")


def message(to, sender="pat@firm-a", attachments=(("lease.pdf", make_pdf(["LEASE"]), "application", "pdf"),)):
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = f"Pat <{sender}>", to, "Signed lease for filing"
    msg.set_content("Please file the attached.")
    for name, data, main, sub in attachments:
        msg.add_attachment(data, maintype=main, subtype=sub, filename=name)
    return msg.as_bytes()


def post(client, raw, token="hook-token"):
    return client.post("/inbound/email", content=raw, headers={"X-Provenant-Inbound-Token": token,
                                                               "Content-Type": "message/rfc822"})


def test_each_firm_has_a_private_address(mailroom, monkeypatch):
    assert mailroom.startswith("firm-a.") and mailroom.endswith("@in.provenant.test")
    assert inbox.tenant_for([mailroom.upper()]) == "firm-a"
    forged = mailroom.replace("firm-a.", "firm-b.")
    assert inbox.tenant_for([forged]) is None  # changing the firm breaks the signature
    assert inbox.inbound_address("Firm-A") is None


def test_attachments_from_a_member_arrive_in_the_inbox_and_are_filed_by_a_person(client, app, mailroom):
    sign_in(client, PREPARER, REVIEWER)
    raw = message(mailroom, attachments=(("lease.pdf", make_pdf(["LEASE AGREEMENT"]), "application", "pdf"),
                                          ("fake.pdf", b"MZ not a pdf", "application", "pdf"),
                                          ("run.exe", b"MZ", "application", "octet-stream")))
    assert post(client, raw, token="wrong").status_code == 401
    assert post(client, raw).json() == {"kept": 1}
    box = client.get("/inbox", headers=REVIEWER).json()
    assert box["address"] == mailroom and [i["filename"] for i in box["items"]] == ["lease.pdf"]
    assert client.get("/inbox", headers=OTHER_FIRM).json()["items"] == []
    assert client.get("/documents", headers=REVIEWER).json() == []  # nothing is read until someone files it

    item = box["items"][0]["id"]
    filed = client.post(f"/inbox/{item}/file", headers=REVIEWER, json={"kind": "lease"}).json()
    assert filed["status"] == "filed" and filed["document_id"]
    doc = client.get(f"/documents/{filed['document_id']}", headers=REVIEWER).json()
    assert doc["uploaded_by"] == "rae@firm-a"  # whoever files it is the uploader, for maker-checker
    assert client.post(f"/inbox/{item}/dismiss", headers=REVIEWER).status_code == 409


def test_mail_from_strangers_or_to_unknown_addresses_is_dropped(client, mailroom):
    sign_in(client, PREPARER)
    assert post(client, message(mailroom, sender="someone@else.test")).json()["kept"] == 0
    assert post(client, message("firm-a.0000000000000000@in.provenant.test")).json()["kept"] == 0
    assert client.get("/inbox", headers=PREPARER).json()["items"] == []
