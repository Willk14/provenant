"""Identity. Production: OIDC access tokens (RS256/ES256) verified against the issuer's JWKS."""
import os
import re
from collections.abc import Callable
from dataclasses import dataclass

import jwt

PREPARER, REVIEWER, PARTNER = "preparer", "reviewer", "partner"
ROLES = frozenset({PREPARER, REVIEWER, PARTNER})
_TENANT = re.compile(r"[A-Za-z0-9_-]{1,64}")


class AuthError(Exception):
    pass


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    user: str
    roles: frozenset[str]

    def has_any(self, *roles: str) -> bool:
        return not self.roles.isdisjoint(roles)


Authenticator = Callable[[str], Principal]


def principal_from(tenant_id: object, user: object, roles: object) -> Principal:
    if not isinstance(tenant_id, str) or not _TENANT.fullmatch(tenant_id):
        raise AuthError("missing or invalid tenant")
    if not isinstance(user, str) or not 0 < len(user) <= 200:
        raise AuthError("missing or invalid user")
    if isinstance(roles, str):
        roles = roles.split(",")
    if not isinstance(roles, (list, tuple)) or not all(isinstance(r, str) for r in roles):
        raise AuthError("roles must be a list of strings")
    return Principal(tenant_id, user, frozenset(r.strip() for r in roles) & ROLES)


class JWTAuthenticator:
    """Verifies signature, expiry, issuer and audience. Identity is `sub` (stable), not email (mutable)."""

    def __init__(
        self,
        issuer: str,
        audience: str,
        *,
        jwks_url: str | None = None,
        key: object | None = None,
        tenant_claim: str = "tenant_id",
        roles_claim: str = "roles",
        algorithms: tuple[str, ...] = ("RS256", "ES256"),
    ) -> None:
        if (jwks_url is None) == (key is None):
            raise ValueError("provide exactly one of jwks_url or key")
        self._jwks = jwt.PyJWKClient(jwks_url, cache_keys=True) if jwks_url else None
        self._key = key
        self.issuer, self.audience = issuer, audience
        self.tenant_claim, self.roles_claim, self.algorithms = tenant_claim, roles_claim, list(algorithms)

    def __call__(self, token: str) -> Principal:
        try:
            key = self._jwks.get_signing_key_from_jwt(token).key if self._jwks else self._key
            claims = jwt.decode(
                token,
                key,
                algorithms=self.algorithms,
                audience=self.audience,
                issuer=self.issuer,
                leeway=30,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise AuthError(f"invalid token: {exc}") from exc
        return principal_from(claims.get(self.tenant_claim), claims["sub"], claims.get(self.roles_claim, []))


def authenticator_from_env() -> Authenticator | None:
    issuer = os.environ.get("PROVENANT_JWT_ISSUER")
    if not issuer:
        return None
    return JWTAuthenticator(
        issuer,
        os.environ["PROVENANT_JWT_AUDIENCE"],
        jwks_url=os.environ["PROVENANT_JWKS_URL"],
        tenant_claim=os.environ.get("PROVENANT_JWT_TENANT_CLAIM", "tenant_id"),
        roles_claim=os.environ.get("PROVENANT_JWT_ROLES_CLAIM", "roles"),
    )
