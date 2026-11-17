import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from provenant.api.auth import AuthError, JWTAuthenticator
from provenant.api.main import create_app

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
ISSUER, AUDIENCE = "https://idp.example.test/", "provenant-api"
AUTH = JWTAuthenticator(ISSUER, AUDIENCE, key=KEY.public_key())


def token(key=KEY, **overrides) -> str:
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "user-123",
        "exp": int(time.time()) + 300,
        "tenant_id": "firm-a",
        "roles": ["reviewer", "not-a-role"],
    } | overrides
    return jwt.encode({k: v for k, v in claims.items() if v is not None}, key, algorithm="RS256")


def test_valid_token_yields_principal():
    p = AUTH(token())
    assert (p.tenant_id, p.user, p.roles) == ("firm-a", "user-123", frozenset({"reviewer"}))


@pytest.mark.parametrize(
    "bad",
    [
        token(key=OTHER_KEY),  # wrong signature
        token(exp=int(time.time()) - 3600),  # expired
        token(aud="someone-else"),
        token(iss="https://evil.test/"),
        token(sub=None),
        token(tenant_id=None),
        token(tenant_id="../etc"),
        jwt.encode({"sub": "x"}, "secret", algorithm="HS256"),  # algorithm not allowed
    ],
)
def test_bad_tokens_are_rejected(bad):
    with pytest.raises(AuthError):
        AUTH(bad)


def test_api_uses_bearer_tokens(database_url, tmp_path):
    app = create_app(database_url, tmp_path / "s", authenticator=AUTH)
    with TestClient(app) as c:
        assert c.get("/review-queue").status_code == 401
        assert c.get("/review-queue", headers={"X-Tenant-Id": "firm-a", "X-User": "x"}).status_code == 401
        assert c.get("/review-queue", headers={"Authorization": f"Bearer {token()}"}).status_code == 200
        no_roles = token(roles=["intern"])
        assert c.get("/review-queue", headers={"Authorization": f"Bearer {no_roles}"}).status_code == 403


def test_dev_headers_cannot_be_combined_with_jwt(database_url, tmp_path):
    with pytest.raises(RuntimeError):
        create_app(database_url, tmp_path / "s", authenticator=AUTH, dev_auth=True)
