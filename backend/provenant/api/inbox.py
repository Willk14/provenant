"""Documents received by email (ADR 0017).

Each firm has a private address, `<tenant>.<signature>@<inbound domain>`, whose signature is an HMAC of the firm's id,
so no lookup table is needed and the address cannot be guessed. An inbound mail service posts the raw message here
with a shared token. Only attachments from active members of that firm are kept, and nothing is read or used until a
person files it.
"""
import email
import email.policy
import hashlib
import hmac
import logging
import os
import re
from datetime import UTC, datetime
from email.utils import getaddresses, parseaddr
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from provenant.api import services
from provenant.api.auth import PREPARER, REVIEWER, PARTNER, Principal
from provenant.api.db import InboxItemRow, MemberRow, tenant_session
from provenant.api.services import Conflict, Invalid, NotFound, require
from provenant.api.storage import BlobStore
from provenant.api.tax import ATTACHMENT_TYPES

log = logging.getLogger(__name__)
MAX_MESSAGE_BYTES = 40 * 1024 * 1024
MAX_ATTACHMENTS = 20
_LOCAL = re.compile(r"([a-z0-9_-]{1,64})\.([0-9a-f]{16})")


def _secret() -> bytes:
    return os.environ.get("PROVENANT_INBOUND_SECRET", "").encode()


def _signature(tenant_id: str) -> str:
    return hmac.new(_secret(), tenant_id.encode(), hashlib.sha256).hexdigest()[:16]


def inbound_address(tenant_id: str) -> str | None:
    domain = os.environ.get("PROVENANT_INBOUND_DOMAIN", "").strip().lower()
    if not (domain and _secret()) or tenant_id != tenant_id.lower():
        return None  # mail servers may lower-case addresses, so only lower-case firm ids get one
    return f"{tenant_id}.{_signature(tenant_id)}@{domain}"


def tenant_for(addresses: list[str]) -> str | None:
    """The firm a message was sent to, from any recipient with a valid signature on the inbound domain."""
    domain = os.environ.get("PROVENANT_INBOUND_DOMAIN", "").strip().lower()
    if not (domain and _secret()):
        return None
    for address in addresses:
        local, _, host = address.strip().lower().partition("@")
        match = _LOCAL.fullmatch(local)
        if host == domain and match and hmac.compare_digest(match[2], _signature(match[1])):
            return match[1]
    return None


def _allowed(filename: str, data: bytes) -> str | None:
    """The media type, if this attachment is a kind Provenant accepts and its bytes match its name."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ATTACHMENT_TYPES or not data or len(data) > services.MAX_UPLOAD_BYTES:
        return None
    media_type, magic = ATTACHMENT_TYPES[ext]
    if magic is None:
        try:
            data.decode("utf-8-sig")
        except UnicodeDecodeError:
            return None
    elif not data.startswith(magic):
        return None
    return media_type


def receive(factory: sessionmaker[Session], store: BlobStore, raw: bytes) -> dict:
    """Keep the attachments of one raw message. Unknown firms and senders are dropped without saying why."""
    message = email.message_from_bytes(raw, policy=email.policy.default)
    recipients = [a for _, a in getaddresses(message.get_all("to", []) + message.get_all("cc", []) +
                                              message.get_all("delivered-to", []) + message.get_all("x-original-to", []))]
    tenant_id = tenant_for(recipients)
    sender = parseaddr(str(message.get("from", "")))[1].strip().lower()
    if tenant_id is None or not sender:
        return {"kept": 0, "reason": "not addressed to a firm"}
    with tenant_session(factory, tenant_id) as s:
        member = s.scalar(select(MemberRow).where(MemberRow.tenant_id == tenant_id, MemberRow.status == "active",
                                                  func.lower(MemberRow.email) == sender))
        if member is None:
            log.warning("inbound email for %s from a sender who is not an active member was dropped", tenant_id)
            return {"kept": 0, "reason": "sender is not a member"}
        subject = " ".join(str(message.get("subject", "")).split())[:200]
        kept = 0
        for part in message.iter_attachments():
            if kept >= MAX_ATTACHMENTS:
                break
            filename = (part.get_filename() or "").strip()[:255]
            data = part.get_payload(decode=True) or b""
            media_type = _allowed(filename, data)
            if media_type is None:
                continue
            sha = hashlib.sha256(data).hexdigest()
            store.put(sha, data)
            s.add(InboxItemRow(tenant_id=tenant_id, sender=sender, subject=subject, filename=filename, sha256=sha,
                               content_type=media_type, size_bytes=len(data), status="new"))
            kept += 1
        s.commit()
    return {"kept": kept}


def get_item(s: Session, tenant_id: str, item_id: str) -> InboxItemRow:
    row = s.scalar(select(InboxItemRow).where(InboxItemRow.tenant_id == tenant_id, InboxItemRow.id == item_id))
    if row is None:
        raise NotFound("inbox item not found")
    return row


def file_item(s: Session, store: BlobStore, p: Principal, item_id: str, kind: str) -> InboxItemRow:
    """File an attachment as a contract. The person who files it is its uploader, for maker-checker."""
    require(p, PREPARER, REVIEWER, PARTNER)
    row = get_item(s, p.tenant_id, item_id)
    if row.status != "new":
        raise Conflict(f"this item was already {row.status}")
    if row.content_type != "application/pdf":
        raise Invalid("only PDFs can be filed as contracts; download other files and upload them to the client")
    doc, _ = services.upload_document(s, store, p, row.filename, store.get(row.sha256), kind=kind)
    row = get_item(s, p.tenant_id, item_id)
    row.status, row.document_id, row.handled_by, row.handled_at = "filed", doc.id, p.user, datetime.now(UTC)
    s.commit()
    return row


def dismiss_item(s: Session, p: Principal, item_id: str) -> InboxItemRow:
    require(p, PREPARER, REVIEWER, PARTNER)
    row = get_item(s, p.tenant_id, item_id)
    if row.status != "new":
        raise Conflict(f"this item was already {row.status}")
    row.status, row.handled_by, row.handled_at = "dismissed", p.user, datetime.now(UTC)
    s.commit()
    return row


class InboxItemOut(BaseModel):
    id: str
    sender: str
    subject: str
    filename: str
    content_type: str
    size_bytes: int
    status: str
    document_id: str | None
    received_at: datetime

    model_config = {"from_attributes": True}


class InboxOut(BaseModel):
    address: str | None
    items: list[InboxItemOut]


class FileIn(BaseModel):
    kind: Literal["lease", "credit_agreement"]


def add_routes(app: FastAPI, Auth, Db, factory: sessionmaker[Session], store: BlobStore) -> None:  # noqa: N803
    @app.post("/inbound/email")
    async def inbound(request: Request) -> dict:
        """Called by the inbound mail service, not by people. It needs the shared token, and takes the raw message
        as the body or as a multipart field named `email` (SendGrid's "raw" option)."""
        expected = os.environ.get("PROVENANT_INBOUND_TOKEN", "")
        given = request.headers.get("x-provenant-inbound-token") or request.query_params.get("token") or ""
        if not expected or not hmac.compare_digest(given.encode(), expected.encode()):
            raise HTTPException(401, "inbound token required")
        if int(request.headers.get("content-length") or 0) > MAX_MESSAGE_BYTES:
            raise HTTPException(413, "message too large")
        if request.headers.get("content-type", "").startswith("multipart/form-data"):
            form = await request.form()
            field = form.get("email")
            raw = (await field.read()) if hasattr(field, "read") else str(field or "").encode()
        else:
            raw = await request.body()
        if len(raw) > MAX_MESSAGE_BYTES:
            raise HTTPException(413, "message too large")
        from starlette.concurrency import run_in_threadpool

        return await run_in_threadpool(receive, factory, store, raw)

    @app.get("/inbox", response_model=InboxOut)
    def get_inbox(p: Auth, s: Db) -> InboxOut:
        rows = s.scalars(select(InboxItemRow).where(InboxItemRow.tenant_id == p.tenant_id, InboxItemRow.status == "new")
                         .order_by(InboxItemRow.received_at.desc()).limit(100))
        return InboxOut(address=inbound_address(p.tenant_id), items=[InboxItemOut.model_validate(r) for r in rows])

    @app.get("/inbox/{item_id}/file")
    def download(item_id: str, p: Auth, s: Db) -> Response:
        row = get_item(s, p.tenant_id, item_id)
        safe = re.sub(r'[^A-Za-z0-9._ -]', "_", row.filename) or "attachment"
        return Response(store.get(row.sha256), media_type=row.content_type,
                        headers={"Content-Disposition": f'attachment; filename="{safe}"'})

    @app.post("/inbox/{item_id}/file", response_model=InboxItemOut)
    def file(item_id: str, body: FileIn, p: Auth, s: Db) -> InboxItemOut:
        return InboxItemOut.model_validate(file_item(s, store, p, item_id, body.kind))

    @app.post("/inbox/{item_id}/dismiss", response_model=InboxItemOut)
    def dismiss(item_id: str, p: Auth, s: Db) -> InboxItemOut:
        return InboxItemOut.model_validate(dismiss_item(s, p, item_id))
