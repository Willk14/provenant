"""Locked precedents and template-driven drafts for a matter (ADR 0019)."""
import json
from datetime import date
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from provenant.api.auth import PARTNER, PREPARER, REVIEWER, Principal
from provenant.api.db import MatterPartyRow, PrecedentDraftRow, PrecedentRow, PrecedentVersionRow
from provenant.api.matters import _event, get_matter
from provenant.api.services import Conflict, Forbidden, Invalid, NotFound, require
from provenant.practice import precedents as engine

ANY = (PREPARER, REVIEWER, PARTNER)
KINDS = ("engagement_letter", "nda", "lease", "employment", "pleading", "letter", "other")


def _version(s: Session, tenant_id: str, precedent_id: str, version: int) -> PrecedentVersionRow:
    row = s.scalar(select(PrecedentVersionRow).where(PrecedentVersionRow.tenant_id == tenant_id,
                                                     PrecedentVersionRow.precedent_id == precedent_id,
                                                     PrecedentVersionRow.version == version))
    if row is None:
        raise NotFound("precedent version not found")
    return row


def get_precedent(s: Session, tenant_id: str, precedent_id: str) -> PrecedentRow:
    row = s.scalar(select(PrecedentRow).where(PrecedentRow.tenant_id == tenant_id, PrecedentRow.id == precedent_id))
    if row is None:
        raise NotFound("precedent not found")
    return row


def lock_version(s: Session, p: Principal, precedent: PrecedentRow, body: str) -> PrecedentVersionRow:
    try:
        engine.check(body)
    except ValueError as exc:
        raise Invalid(str(exc)) from exc
    version = PrecedentVersionRow(tenant_id=p.tenant_id, precedent_id=precedent.id, version=precedent.current_version + 1,
                                  body=body, sha256=engine.sha(body), locked_by=p.user)
    precedent.current_version = version.version
    s.add(version)
    return version


def matter_facts(s: Session, matter) -> dict[str, str]:
    """The facts a precedent can use without anyone typing them: names come from the matter's parties."""
    parties = list(s.scalars(select(MatterPartyRow).where(MatterPartyRow.tenant_id == matter.tenant_id,
                                                          MatterPartyRow.matter_id == matter.id)))
    names = lambda role: " and ".join(x.name for x in parties if x.role == role)  # noqa: E731
    return {"client_name": names("client"), "counterparty_name": names("counterparty"),
            "matter_reference": matter.reference, "matter_title": matter.title, "responsible": matter.responsible or "",
            "date": f"{date.today().day} {date.today():%B %Y}"}


class PrecedentIn(BaseModel):
    name: str = Field(min_length=3, max_length=120)
    kind: Literal["engagement_letter", "nda", "lease", "employment", "pleading", "letter", "other"]
    body: str = Field(max_length=engine.MAX_BODY)


class VersionIn(BaseModel):
    body: str = Field(max_length=engine.MAX_BODY)


class DraftIn(BaseModel):
    precedent_id: str = Field(max_length=36)
    values: dict[str, str] = Field(default_factory=dict, max_length=60)
    include: list[str] = Field(default_factory=list, max_length=60)


class DraftEditIn(BaseModel):
    text: str = Field(max_length=engine.MAX_BODY)


class DraftDecisionIn(BaseModel):
    decision: Literal["approved", "returned"]
    note: str = Field(default="", max_length=1000)


def _precedent_out(s: Session, row: PrecedentRow) -> dict:
    current = _version(s, row.tenant_id, row.id, row.current_version)
    return {"id": row.id, "name": row.name, "kind": row.kind, "version": row.current_version, "body": current.body,
            "fields": engine.fields(current.body), "options": engine.options(current.body),
            "locked_by": current.locked_by, "locked_at": current.locked_at.isoformat()}


def _draft_out(s: Session, row: PrecedentDraftRow) -> dict:
    precedent = s.get(PrecedentRow, row.precedent_id)
    return {"id": row.id, "precedent": precedent.name if precedent else "", "version": row.version,
            "status": row.status, "text": row.current_text, "unfilled": engine.unfilled(row.current_text),
            "deviations": [vars(d) for d in engine.deviations(row.standard_text, row.current_text)],
            "values": json.loads(row.field_values), "created_by": row.created_by, "edited_by": row.edited_by,
            "decided_by": row.decided_by, "decision_note": row.decision_note, "updated_at": row.updated_at.isoformat()}


def _get_draft(s: Session, tenant_id: str, matter_id: str, draft_id: str) -> PrecedentDraftRow:
    row = s.scalar(select(PrecedentDraftRow).where(PrecedentDraftRow.tenant_id == tenant_id,
                                                   PrecedentDraftRow.matter_id == matter_id,
                                                   PrecedentDraftRow.id == draft_id))
    if row is None:
        raise NotFound("draft not found")
    return row


def add_routes(app: FastAPI, Auth, Db) -> None:  # noqa: N803 - FastAPI dependency aliases
    @app.get("/precedents")
    def list_precedents(p: Auth, s: Db) -> list[dict]:
        rows = s.scalars(select(PrecedentRow).where(PrecedentRow.tenant_id == p.tenant_id).order_by(PrecedentRow.name))
        return [_precedent_out(s, r) for r in rows]

    @app.post("/precedents", status_code=201)
    def create_precedent(body: PrecedentIn, p: Auth, s: Db) -> dict:
        """Only a partner locks a precedent. Nobody edits a locked version; a change is a new version."""
        require(p, PARTNER)
        row = PrecedentRow(tenant_id=p.tenant_id, name=body.name.strip(), kind=body.kind, current_version=0,
                           created_by=p.user)
        s.add(row)
        s.flush()
        lock_version(s, p, row, body.body)
        s.commit()
        return _precedent_out(s, row)

    @app.post("/precedents/{precedent_id}/versions", status_code=201)
    def new_version(precedent_id: str, body: VersionIn, p: Auth, s: Db) -> dict:
        require(p, PARTNER)
        row = get_precedent(s, p.tenant_id, precedent_id)
        lock_version(s, p, row, body.body)
        s.commit()
        return _precedent_out(s, row)

    @app.get("/matters/{matter_id}/precedent-drafts")
    def list_drafts(matter_id: str, p: Auth, s: Db) -> dict:
        matter = get_matter(s, p.tenant_id, matter_id)
        rows = s.scalars(select(PrecedentDraftRow).where(PrecedentDraftRow.tenant_id == p.tenant_id,
                                                         PrecedentDraftRow.matter_id == matter.id)
                         .order_by(PrecedentDraftRow.created_at.desc()))
        return {"facts": matter_facts(s, matter), "drafts": [_draft_out(s, r) for r in rows]}

    @app.post("/matters/{matter_id}/precedent-drafts", status_code=201)
    def create_draft(matter_id: str, body: DraftIn, p: Auth, s: Db) -> dict:
        require(p, *ANY)
        matter = get_matter(s, p.tenant_id, matter_id)
        if matter.status != "open":
            raise Conflict("draft from precedents once the matter's conflict check is cleared")
        precedent = get_precedent(s, p.tenant_id, body.precedent_id)
        version = _version(s, p.tenant_id, precedent.id, precedent.current_version)
        values = {**matter_facts(s, matter), **{k: " ".join(v.split())[:500] for k, v in body.values.items() if v.strip()}}
        unknown = set(body.include) - set(engine.options(version.body))
        if unknown:
            raise Invalid(f"the precedent has no optional clause {sorted(unknown)[0]!r}")
        text = engine.render(version.body, values, set(body.include))
        row = PrecedentDraftRow(tenant_id=p.tenant_id, matter_id=matter.id, precedent_id=precedent.id,
                                version=version.version, field_values=json.dumps(values),
                                included=json.dumps(sorted(body.include)), standard_text=text, current_text=text,
                                status="ready", created_by=p.user)
        s.add(row)
        _event(s, matter, "precedent_drafted", p.user, f"{precedent.name} v{version.version}")
        s.commit()
        return _draft_out(s, row)

    @app.put("/matters/{matter_id}/precedent-drafts/{draft_id}")
    def edit_draft(matter_id: str, draft_id: str, body: DraftEditIn, p: Auth, s: Db) -> dict:
        require(p, *ANY)
        row = _get_draft(s, p.tenant_id, matter_id, draft_id)
        if row.status == "approved":
            raise Conflict("an approved draft is locked")
        if not body.text.strip():
            raise Invalid("the draft cannot be empty")
        row.current_text, row.edited_by, row.status = body.text.strip() + "\n", p.user, "ready"
        s.commit()
        return _draft_out(s, row)

    @app.post("/matters/{matter_id}/precedent-drafts/{draft_id}/decision")
    def decide(matter_id: str, draft_id: str, body: DraftDecisionIn, p: Auth, s: Db) -> dict:
        """A reviewer or partner who neither made nor edited the draft decides. Departures from the precedent need a
        partner, who must say why they are acceptable."""
        require(p, REVIEWER, PARTNER)
        matter = get_matter(s, p.tenant_id, matter_id)
        row = _get_draft(s, p.tenant_id, matter_id, draft_id)
        if row.status != "ready":
            raise Conflict("only a draft waiting for review can be decided")
        if p.user in {row.created_by, row.edited_by}:
            raise Forbidden("maker-checker: someone who did not make or edit the draft must decide")
        note = " ".join(body.note.split())
        changed = engine.deviations(row.standard_text, row.current_text)
        if body.decision == "returned" and len(note) < 5:
            raise Invalid("say what needs to change")
        if body.decision == "approved":
            if engine.unfilled(row.current_text):
                raise Conflict("fill in every [[field]] before approving")
            if changed and not p.has_any(PARTNER):
                raise Forbidden("this draft departs from the precedent, so a partner must approve it")
            if changed and len(note) < 5:
                raise Invalid("say why the departures from the precedent are acceptable")
        row.status, row.decided_by, row.decision_note = body.decision, p.user, note[:1000] or None
        _event(s, matter, f"precedent_{body.decision}", p.user,
               f"{len(changed)} departure{'s' if len(changed) != 1 else ''} from the precedent" + (f": {note}" if note else ""))
        s.commit()
        return _draft_out(s, row)


__all__ = ["KINDS", "add_routes"]
