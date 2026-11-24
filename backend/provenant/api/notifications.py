"""Work notifications by email (ADR 0017).

An email says that something is waiting and links to it. It never contains contract text, figures, client names or
file names, because email leaves the firm's control. Rows are queued in the same transaction as the work and sent by
the worker through the firm's SMTP server.
"""
import logging
import os
import smtplib
from collections.abc import Iterable
from datetime import UTC, datetime
from email.message import EmailMessage

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from provenant.api.db import Job, MemberRow, NotificationRow, tenant_session

log = logging.getLogger(__name__)
NOTIFY_JOB = "notify"
MAX_ATTEMPTS = 3
BATCH = 50


def smtp_configured() -> bool:
    return bool(os.environ.get("PROVENANT_SMTP_HOST") and os.environ.get("PROVENANT_SMTP_FROM"))


def send_email(to: str, subject: str, body: str) -> None:
    """Send one plain-text email through the configured server. Port 465 uses TLS throughout; others use STARTTLS."""
    host, sender = os.environ["PROVENANT_SMTP_HOST"], os.environ["PROVENANT_SMTP_FROM"]
    port = int(os.environ.get("PROVENANT_SMTP_PORT", "587"))
    user, password = os.environ.get("PROVENANT_SMTP_USER", ""), os.environ.get("PROVENANT_SMTP_PASSWORD", "")
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = sender, to, subject
    message.set_content(body)
    connect = smtplib.SMTP_SSL if port == 465 else smtplib.SMTP
    with connect(host, port, timeout=20) as server:
        if port != 465:
            server.starttls()
        if user:
            server.login(user, password)
        server.send_message(message)


def _link(path: str) -> str:
    base = os.environ.get("PROVENANT_APP_URL", "").rstrip("/")
    return f"{base}{path}" if base else path


def notify(s: Session, tenant_id: str, kind: str, subject: str, text: str, path: str, *,
           roles: Iterable[str] = (), people: Iterable[str] = (), exclude: Iterable[str] = ()) -> int:
    """Queue an email to active members with any of `roles`, or to the named `people` (sign-in id or email), who
    have notifications on, leaving out `exclude`. Call before the caller commits. Returns how many were queued."""
    roles, people = set(roles), {x.lower() for x in people}
    excluded = {x.lower() for x in exclude}
    members = s.scalars(select(MemberRow).where(MemberRow.tenant_id == tenant_id, MemberRow.status == "active",
                                                MemberRow.notify.is_(True))).all()
    chosen = []
    for m in members:
        ids = {m.email.lower(), (m.user_id or "").lower()} - {""}
        if ids & excluded:
            continue
        if ids & people or (roles and roles & set(m.roles.split(","))):
            chosen.append(m.email)
    body = f"{text}\n\nOpen it in Provenant: {_link(path)}\n\nYou can turn these emails off on the Team page."
    for email in sorted(set(chosen)):
        s.add(NotificationRow(tenant_id=tenant_id, email=email, kind=kind, subject=subject[:200], body=body,
                              status="queued"))
    if chosen:
        s.add(Job(tenant_id=tenant_id, kind=NOTIFY_JOB, document_id=""))
    return len(set(chosen))


def notify_safely(factory: sessionmaker[Session], tenant_id: str, kind: str, subject: str, text: str, path: str,
                  **who) -> None:
    """For use after the work has committed: a notification problem must never undo or fail the work."""
    try:
        with tenant_session(factory, tenant_id) as s:
            notify(s, tenant_id, kind, subject, text, path, **who)
            s.commit()
    except Exception:
        log.exception("could not queue %s notification", kind)


def deliver(factory: sessionmaker[Session], tenant_id: str) -> None:
    """Send queued emails. Without an SMTP server each is marked skipped, so nothing waits forever."""
    configured = smtp_configured()
    with tenant_session(factory, tenant_id) as s:
        rows = s.scalars(select(NotificationRow).where(NotificationRow.tenant_id == tenant_id,
                                                       NotificationRow.status == "queued")
                         .order_by(NotificationRow.created_at).limit(BATCH)).all()
        retry = False
        for row in rows:
            if not configured:
                row.status, row.error = "skipped", "no SMTP server configured"
                continue
            try:
                send_email(row.email, row.subject, row.body)
                row.status, row.sent_at, row.error = "sent", datetime.now(UTC), None
            except Exception as exc:
                row.attempts += 1
                row.error = f"{type(exc).__name__}: {exc}"[:300]
                if row.attempts >= MAX_ATTEMPTS:
                    row.status = "failed"
                else:
                    retry = True
        s.commit()
    if retry:
        raise RuntimeError("some notifications could not be sent yet")  # the worker retries with backoff
