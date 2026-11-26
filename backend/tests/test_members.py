"""Sign-in through Supabase claims, the staff directory and invitations."""
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from provenant.api import members
from provenant.api.auth import AuthError, JWTAuthenticator, authenticator_from_env
from provenant.api.main import create_app
from tests.test_api import OTHER_FIRM, PARTNER, PREPARER, who

KEY = ec.generate_private_key(ec.SECP256R1())
ISSUER = "https://abcdefghijklmnopqrst.supabase.co/auth/v1"
SUPABASE = JWTAuthenticator(ISSUER, "authenticated", key=KEY.public_key(), tenant_claim="app_metadata.tenant_id",
                            roles_claim="app_metadata.roles")


def supabase_token(**overrides) -> str:
    claims = {"iss": ISSUER, "aud": "authenticated", "sub": "0f6c7d2e-user", "exp": int(time.time()) + 300,
              "email": "wanjiku@firm.co.ke", "role": "authenticated",
              "app_metadata": {"provider": "email", "tenant_id": "firm-a", "roles": ["reviewer"]},
              "user_metadata": {"tenant_id": "firm-b", "roles": ["partner"]}} | overrides
    return jwt.encode(claims, KEY, algorithm="ES256")


def test_supabase_access_is_read_from_app_metadata_only():
    p = SUPABASE(supabase_token())
    assert (p.tenant_id, p.user, p.roles, p.email) == ("firm-a", "0f6c7d2e-user", frozenset({"reviewer"}),
                                                       "wanjiku@firm.co.ke")  # user_metadata is user-editable: ignored
    with pytest.raises(AuthError):
        SUPABASE(supabase_token(app_metadata={"provider": "email"}))  # signed up, but never given a firm
    with pytest.raises(AuthError):
        SUPABASE(supabase_token(aud="anon"))


def test_supabase_settings_point_at_the_project(monkeypatch):
    for name in ("PROVENANT_JWT_ISSUER", "PROVENANT_SUPABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    assert authenticator_from_env() is None
    monkeypatch.setenv("PROVENANT_SUPABASE_URL", "https://abcdefghijklmnopqrst.supabase.co/")
    auth = authenticator_from_env()
    assert (auth.issuer, auth.audience, auth.tenant_claim) == (ISSUER, "authenticated", "app_metadata.tenant_id")


def test_signing_in_adds_a_person_to_the_directory(database_url, tmp_path):
    app = create_app(database_url, tmp_path / "s", authenticator=SUPABASE, dev_auth=False)  # an earlier test sets it
    with TestClient(app) as c:
        bearer = {"Authorization": f"Bearer {supabase_token()}"}
        response = c.get("/me", headers=bearer)
        assert response.status_code == 200, response.text
        me = response.json()
        assert (me["email"], me["roles"], me["notify"]) == ("wanjiku@firm.co.ke", ["reviewer"], True)
        assert c.put("/me/notifications", headers=bearer, json={"notify": False}).json()["notify"] is False
        listed = c.get("/members", headers=bearer).json()
        assert [(m["email"], m["status"]) for m in listed] == [("wanjiku@firm.co.ke", "active")]


def test_only_a_partner_invites_and_nothing_is_sent_without_a_sign_in_service(client, monkeypatch):
    monkeypatch.delenv("PROVENANT_SUPABASE_URL", raising=False)
    body = {"email": "Otieno@Firm.co.ke", "roles": ["preparer"]}
    assert client.post("/members", headers=PREPARER, json=body).status_code == 403
    made = client.post("/members", headers=PARTNER, json=body).json()
    assert made["email_sent"] is False and made["member"]["email"] == "otieno@firm.co.ke"
    assert client.post("/members", headers=PARTNER, json=body).status_code == 409
    assert client.post("/members", headers=PARTNER, json={"email": "not-an-email", "roles": ["preparer"]}).status_code in (400, 422)
    assert client.get("/members", headers=OTHER_FIRM).json() == []


def test_an_invitation_sets_firm_and_roles_in_app_metadata(client, monkeypatch):
    monkeypatch.setenv("PROVENANT_SUPABASE_URL", "https://abcdefghijklmnopqrst.supabase.co")
    monkeypatch.setenv("PROVENANT_SUPABASE_SECRET_KEY", "sb_secret_test")
    monkeypatch.setenv("PROVENANT_APP_URL", "https://app.provenant.test")
    calls = []

    def fake(method, path, body):
        calls.append((method, path, body))
        return {"id": "new-user"} if path == "/invite" else {}

    monkeypatch.setattr(members, "_admin_request", fake)
    made = client.post("/members", headers=who("pam@firm-a", "partner"),
                       json={"email": "akinyi@firm.co.ke", "roles": ["reviewer", "preparer"]}).json()
    assert made["email_sent"] is True
    assert calls == [
        ("POST", "/invite", {"email": "akinyi@firm.co.ke", "redirect_to": "https://app.provenant.test/auth/callback"}),
        ("PUT", "/admin/users/new-user", {"app_metadata": {"tenant_id": "firm-a", "roles": ["preparer", "reviewer"]}}),
    ]


def test_a_refused_invitation_is_not_recorded(client, monkeypatch):
    monkeypatch.setenv("PROVENANT_SUPABASE_URL", "https://abcdefghijklmnopqrst.supabase.co")
    monkeypatch.setenv("PROVENANT_SUPABASE_SECRET_KEY", "sb_secret_test")

    def refuse(method, path, body):
        raise members.InviteFailed("the sign-in service refused it (422): email rate limit exceeded")

    monkeypatch.setattr(members, "_admin_request", refuse)
    response = client.post("/members", headers=PARTNER, json={"email": "x@firm.co.ke", "roles": ["preparer"]})
    assert response.status_code == 409 and "rate limit" in response.json()["detail"]
    assert client.get("/members", headers=PARTNER).json() == []


def test_demo_people_can_be_given_real_inboxes_for_local_email(database_url, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from provenant.api.main import create_app

    monkeypatch.setenv("PROVENANT_DEV_EMAILS", "pat@firm-a=Inbox+Pat@Example.com, broken-pair")
    with TestClient(create_app(database_url, tmp_path / "storage", dev_auth=True)) as c:
        me = c.get("/me", headers={"X-Tenant-Id": "firm-a", "X-User": "pat@firm-a", "X-Roles": "preparer"}).json()
        assert me["user"] == "pat@firm-a"  # sign-in, and so maker-checker, still uses the demo name
        assert me["email"] == "inbox+pat@example.com"
        other = c.get("/me", headers={"X-Tenant-Id": "firm-a", "X-User": "rae@firm-a", "X-Roles": "reviewer"}).json()
        assert other["email"] == "rae@firm-a"
