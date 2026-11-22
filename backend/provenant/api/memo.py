"""Board tax memo use cases and routes: the draft text, rendered against the live computation (ADR 0008)."""
from datetime import date, datetime

from fastapi import FastAPI
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from provenant.analytics import journal
from provenant.api import etims, ledger, tax
from provenant.api import variance as checks_api
from provenant.api.auth import Principal
from provenant.api.db import MemoDraftRow
from provenant.api.services import Conflict, Invalid, require
from provenant.tax import memo

STATUS = {None: "Not uploaded", True: "Explain", False: "Agrees"}


def _draft(s: Session, tenant_id: str, client_id: str, year_end: date) -> MemoDraftRow | None:
    return s.scalar(select(MemoDraftRow).where(MemoDraftRow.tenant_id == tenant_id, MemoDraftRow.client_id == client_id,
                                               MemoDraftRow.year_end == year_end))


def _context(s: Session, tenant_id: str, client_id: str, year_end: date, store=None):
    """Fields and tables from the live computation, compliance checks and approval."""
    client = tax.get_client(s, tenant_id, client_id)
    run = tax.tax_computation(s, tenant_id, client_id, year_end)
    c = run.computation
    latest, current = tax.current_tax_approval(s, tenant_id, client_id, year_end)
    if latest is None:
        approval = "The computation has not been approved by a reviewer."
    elif current:
        approval = f"The computation was approved by {latest.reviewer} on {latest.approved_at:%d %b %Y}."
    else:
        approval = (f"The computation was approved by {latest.reviewer} on {latest.approved_at:%d %b %Y}, "
                    "but has changed since: it needs approving again.")
    rows, open_checks = [], 0
    for check in checks_api.checks(s, tenant_id, client_id, year_end):
        result = check["result"]
        if result is None:
            rows.append((checks_api.CHECK_TITLES[check["key"]], "Not uploaded"))
        elif result.needs_explanation and check["explanation"] is None:
            open_checks += 1
            rows.append((result.title, f"Variance {memo.money(result.variance)}: to be explained"))
        elif check["explanation"] is not None:
            rows.append((result.title, f"Variance {memo.money(result.variance)}: {check['explanation'].text}"))
        else:
            rows.append((result.title, "Agrees within tolerance"))
    journal_text = ""
    if store is not None:
        gl, result, decisions = ledger.analysis(s, store, tenant_id, client_id, year_end)
        if result is not None:
            flags = sum(t.count for t in result.tests if t.applies)
            cleared = sum(1 for d in decisions.values() if d.status == "cleared")
            escalated = sum(1 for d in decisions.values() if d.status == "escalated")
            still_open = max(flags - cleared - escalated, 0)
            journal_text = (f"Journal entry tests ({gl.lines:,} lines in {result.entries:,} entries, tests "
                            f"{journal.TEST_VERSION}) flagged {flags} items: {cleared} cleared by a reviewer, "
                            f"{escalated} escalated, {still_open} open.")
            rows.append(("Journal entry tests", f"{flags} flagged: {cleared} cleared, {escalated} escalated, "
                                                f"{still_open} open"))
        else:
            rows.append(("Journal entry tests", "Not uploaded"))
    rec = etims.run(s, tenant_id, client_id, year_end)
    if rec.reconciliation is None:
        rows.insert(0, ("eTIMS reconciliation", "Not uploaded"))
    else:
        state = f"applied by {rec.ledger.applied_by}" if rec.ledger.applied_by else "not applied to the computation"
        rows.insert(0, ("eTIMS reconciliation", f"Add-back {memo.money(sum((a for _, a in rec.addback), 0))}, {state}"))
    values = memo.fields(c, client.name, f"{year_end:%d %b %Y}", approval=approval, open_checks=open_checks,
                         journal_entries=journal_text)
    return values, memo.tables(c, rows)


def view(s: Session, tenant_id: str, client_id: str, year_end: date, store=None) -> dict:
    draft = _draft(s, tenant_id, client_id, year_end)
    body = draft.body if draft else memo.DEFAULT
    out = {"body": body, "is_default": draft is None, "updated_by": draft.updated_by if draft else None,
           "updated_at": draft.updated_at if draft else None, "blocked": None, "rendered": None, "fields": {}}
    try:
        values, table_rows = _context(s, tenant_id, client_id, year_end, store)
    except Conflict as exc:  # the computation is waiting on the mapping or a lease
        out["blocked"] = str(exc)
        return out
    out["fields"] = values
    try:
        out["rendered"] = memo.render(body, values, table_rows)
    except ValueError as exc:
        out["blocked"] = str(exc)
    return out


def save(s: Session, p: Principal, client_id: str, year_end: date, body: str) -> None:
    """Save the memo text; empty text goes back to the default. Unknown fields are refused."""
    require(p, *tax.ANY_ROLE)
    tax.get_client(s, p.tenant_id, client_id)
    draft = _draft(s, p.tenant_id, client_id, year_end)
    if not body.strip():
        if draft is not None:
            s.delete(draft)
        s.commit()
        return
    unknown = sorted({n for n in memo.FIELD.findall(body)
                      if not (n.startswith("table:") and n[6:] in memo.TABLES) and n not in memo.FIELD_NAMES})
    if unknown:
        raise Invalid(f"unknown fields: {', '.join(unknown)}")
    if draft is None:
        draft = MemoDraftRow(tenant_id=p.tenant_id, client_id=client_id, year_end=year_end)
        s.add(draft)
    draft.body, draft.updated_by = body[:50_000], p.user
    s.commit()


# --- HTTP -------------------------------------------------------------------------------------------------------


class SegmentOut(BaseModel):
    text: str
    field: str | None


class BlockOut(BaseModel):
    kind: str
    segments: list[SegmentOut]
    table: str | None
    rows: list[tuple[str, str, bool]]


class FieldOut(BaseModel):
    key: str
    label: str
    text: str
    source: str


class MemoOut(BaseModel):
    body: str
    is_default: bool
    updated_by: str | None
    updated_at: datetime | None
    blocked: str | None
    blocks: list[BlockOut]
    warnings: list[str]
    fields: list[FieldOut]
    tables: list[str]


class MemoIn(BaseModel):
    body: str = Field(max_length=50_000)


def _out(v: dict) -> MemoOut:
    r = v["rendered"]
    return MemoOut(
        body=v["body"], is_default=v["is_default"], updated_by=v["updated_by"], updated_at=v["updated_at"],
        blocked=v["blocked"],
        blocks=[BlockOut(kind=b.kind, segments=[SegmentOut(text=x.text, field=x.field) for x in b.segments],
                         table=b.table, rows=list(b.rows)) for b in (r.blocks if r else ())],
        warnings=list(r.warnings) if r else [],
        fields=[FieldOut(key=k, label=f.label, text=f.text, source=f.source) for k, f in v["fields"].items()],
        tables=list(memo.TABLES),
    )


def add_routes(app: FastAPI, Auth, Db, store=None) -> None:  # noqa: N803 - the app's Annotated dependency types
    path = "/clients/{client_id}/years/{year_end}/memo"

    @app.get(path, response_model=MemoOut)
    def get_memo(client_id: str, year_end: date, p: Auth, s: Db) -> MemoOut:
        tax.get_client(s, p.tenant_id, client_id)
        return _out(view(s, p.tenant_id, client_id, year_end, store))

    @app.put(path, response_model=MemoOut)
    def put_memo(client_id: str, year_end: date, body: MemoIn, p: Auth, s: Db) -> MemoOut:
        save(s, p, client_id, year_end, body.body)
        return _out(view(s, p.tenant_id, client_id, year_end, store))
