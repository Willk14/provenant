"""The staff directory and invitations (ADR 0017).

Access is decided by the sign-in service's token, never by this table: an invitation sets the firm and roles in
Supabase `app_metadata`, which users cannot edit. This table records who people are and where to email them.
"""
import json
import logging
import os
import re
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from provenant.api.auth import PARTNER, ROLES, Principal, supabase_url
from provenant.api.db import MemberRow
from provenant.api.services import Conflict, Invalid, require

log = logging.getLogger(__name__)
EMAIL = re.compile(r"[^@\s]{1,64}@[^@\s]{1,253}\.[A-Za-z]{2,}")


class InviteFailed(RuntimeError):
    pass


def _admin_request(method: str, path: str, body: dict) -> dict:
    """One call to the Supabase Auth admin API, with the server-only secret key."""
    url, secret = supabase_url(), os.environ.get("PROVENANT_SUPABASE_SECRET_KEY", "")
    request = urllib.request.Request(f"{url}/auth/v1{path}", method=method, data=json.dumps(body).encode(),
                                     headers={"apikey": secret, "Authorization": f"Bearer {secret}",
                                              "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:300].decode(errors="replace")
        raise InviteFailed(f"the sign-in service refused it ({exc.code}): {detail}") from exc
    except OSError as exc:
        raise InviteFailed(f"the sign-in service could not be reached ({type(exc).__name__})") from exc


def send_invitation(email: str, tenant_id: str, roles: list[str]) -> bool:
    """Email a sign-in invitation and grant access. False when no sign-in service is configured (development)."""
    if not (supabase_url() and os.environ.get("PROVENANT_SUPABASE_SECRET_KEY")):
        return False
    redirect = os.environ.get("PROVENANT_APP_URL", "").rstrip("/")
    invited = _admin_request("POST", "/invite", {"email": email, **({"redirect_to": f"{redirect}/auth/callback"}
                                                                    if redirect else {})})
    user_id = invited.get("id")
    if not user_id:
        raise InviteFailed("the sign-in service did not return the invited user")
    _admin_request("PUT", f"/admin/users/{user_id}", {"app_metadata": {"tenant_id": tenant_id, "roles": roles}})
    return True


def record_sign_in(s: Session, p: Principal) -> MemberRow | None:
    """Keep the directory current from the verified token. A member is matched by sign-in id, then by email."""
    row = s.scalar(select(MemberRow).where(MemberRow.tenant_id == p.tenant_id, MemberRow.user_id == p.user))
    if row is None and p.email:
        row = s.scalar(select(MemberRow).where(MemberRow.tenant_id == p.tenant_id,
                                               func.lower(MemberRow.email) == p.email.lower()))
    if row is None:
        if not p.email:
            return None
        row = MemberRow(tenant_id=p.tenant_id, email=p.email.lower())
        s.add(row)
    row.user_id, row.status, row.last_seen_at = p.user, "active", datetime.now(UTC)
    row.roles = ",".join(sorted(p.roles))
    if p.email:
        row.email = p.email.lower()
    s.commit()
    return row


def invite(s: Session, p: Principal, email: str, roles: list[str]) -> tuple[MemberRow, bool]:
    require(p, PARTNER)
    email = email.strip().lower()
    if not EMAIL.fullmatch(email):
        raise Invalid("enter a valid email address")
    roles = sorted(set(roles))
    if not roles or not set(roles) <= ROLES:
        raise Invalid("choose at least one role: preparer, reviewer or partner")
    if s.scalar(select(MemberRow).where(MemberRow.tenant_id == p.tenant_id, MemberRow.email == email)):
        raise Conflict("that person is already in the directory")
    try:
        sent = send_invitation(email, p.tenant_id, roles)
    except InviteFailed as exc:
        raise Conflict(str(exc)) from exc
    row = MemberRow(tenant_id=p.tenant_id, email=email, roles=",".join(roles), status="invited", invited_by=p.user)
    s.add(row)
    s.commit()
    return row, sent


class MemberOut(BaseModel):
    id: str
    email: str
    roles: list[str]
    status: str
    notify: bool
    last_seen_at: datetime | None

    @classmethod
    def of(cls, r: MemberRow) -> "MemberOut":
        return cls(id=r.id, email=r.email, roles=[x for x in r.roles.split(",") if x], status=r.status,
                   notify=r.notify, last_seen_at=r.last_seen_at)


class MeOut(BaseModel):
    user: str
    email: str
    tenant_id: str
    roles: list[str]
    notify: bool


class InviteIn(BaseModel):
    email: str = Field(max_length=320)
    roles: list[Literal["preparer", "reviewer", "partner"]] = Field(min_length=1, max_length=3)


class InviteOut(BaseModel):
    member: MemberOut
    email_sent: bool


class NotifyIn(BaseModel):
    notify: bool


def add_routes(app: FastAPI, Auth, Db) -> None:  # noqa: N803 - FastAPI dependency aliases
    @app.get("/me", response_model=MeOut)
    def me(p: Auth, s: Db) -> MeOut:
        row = record_sign_in(s, p)
        return MeOut(user=p.user, email=p.email or (row.email if row else ""), tenant_id=p.tenant_id,
                     roles=sorted(p.roles), notify=row.notify if row else False)

    @app.put("/me/notifications", response_model=MeOut)
    def set_notify(body: NotifyIn, p: Auth, s: Db) -> MeOut:
        row = record_sign_in(s, p)
        if row is None:
            raise Conflict("your sign-in has no email address to notify")
        row.notify = body.notify
        s.commit()
        return MeOut(user=p.user, email=row.email, tenant_id=p.tenant_id, roles=sorted(p.roles), notify=row.notify)

    @app.get("/members", response_model=list[MemberOut])
    def members(p: Auth, s: Db) -> list[MemberOut]:
        rows = s.scalars(select(MemberRow).where(MemberRow.tenant_id == p.tenant_id).order_by(MemberRow.email))
        return [MemberOut.of(r) for r in rows]

    @app.post("/members", response_model=InviteOut, status_code=201)
    def invite_member(body: InviteIn, p: Auth, s: Db) -> InviteOut:
        row, sent = invite(s, p, body.email, list(body.roles))
        return InviteOut(member=MemberOut.of(row), email_sent=sent)
