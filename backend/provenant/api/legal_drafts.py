"""Paralegal drafting: a person asks, the agent drafts from the contract, a different person approves (ADR 0015)."""
import json
import logging
from datetime import datetime
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from provenant.api.auth import PARTNER, PREPARER, REVIEWER, Principal
from provenant.api.db import ContractDraftEventRow, ContractDraftRow, Job, tenant_session
from provenant.api.legal import ACTIVE, _citation, _contract, _documents, _failure, _retriever
from provenant.api.legal_routes import CitationOut
from provenant.api.notifications import notify, notify_safely
from provenant.api.services import Conflict, Forbidden, Invalid, NotFound, get_document, list_amendments, require
from provenant.api.storage import BlobStore
from provenant.legal.agent import ReviewAgent
from provenant.legal.drafting import (
    DRAFT_KINDS,
    MAX_INSTRUCTIONS,
    MAX_PARAGRAPH,
    MIN_INSTRUCTIONS,
    DraftToolbox,
    content_hash,
    placeholders,
)

log = logging.getLogger(__name__)
DRAFT_JOB = "legal_draft"


def start_draft(s: Session, p: Principal, document_id: str, kind: str, instructions: str) -> ContractDraftRow:
    require(p, PREPARER, REVIEWER, PARTNER)
    doc = _contract(s, p.tenant_id, document_id)
    if kind not in DRAFT_KINDS:
        raise Invalid("a draft is a notice, amendment or letter")
    instructions = instructions.strip()
    if not MIN_INSTRUCTIONS <= len(instructions) <= MAX_INSTRUCTIONS:
        raise Invalid(f"say what the draft must do, in {MIN_INSTRUCTIONS} to {MAX_INSTRUCTIONS} characters")
    if any(d.status != "extracted" for d in (doc, *list_amendments(s, p.tenant_id, doc.id))):
        raise Conflict("wait until the contract and its amendments have been read")
    if _active(s, p.tenant_id, doc.id):
        raise Conflict("a draft of this contract is already being prepared")
    row = ContractDraftRow(tenant_id=p.tenant_id, document_id=doc.id, kind=kind, instructions=instructions,
                           status="queued", requested_by=p.user)
    s.add(row)
    s.add(Job(tenant_id=p.tenant_id, kind=DRAFT_JOB, document_id=doc.id))  # commits with the draft (outbox)
    s.commit()
    return row


def _active(s: Session, tenant_id: str, document_id: str) -> ContractDraftRow | None:
    return s.scalar(select(ContractDraftRow).where(
        ContractDraftRow.tenant_id == tenant_id, ContractDraftRow.document_id == document_id,
        ContractDraftRow.status.in_(ACTIVE)).order_by(ContractDraftRow.created_at).limit(1))


def list_drafts(s: Session, tenant_id: str, document_id: str) -> list[ContractDraftRow]:
    doc = _contract(s, tenant_id, document_id)
    return list(s.scalars(select(ContractDraftRow).where(
        ContractDraftRow.tenant_id == tenant_id, ContractDraftRow.document_id == doc.id,
    ).order_by(ContractDraftRow.created_at.desc())))


def get_draft(s: Session, tenant_id: str, document_id: str, draft_id: str) -> ContractDraftRow:
    row = s.scalar(select(ContractDraftRow).where(ContractDraftRow.tenant_id == tenant_id,
                                                  ContractDraftRow.document_id == document_id,
                                                  ContractDraftRow.id == draft_id))
    if row is None:
        raise NotFound("draft not found")
    return row


def events(s: Session, tenant_id: str, draft_id: str) -> list[ContractDraftEventRow]:
    rows = s.scalars(select(ContractDraftEventRow).where(ContractDraftEventRow.tenant_id == tenant_id,
                                                         ContractDraftEventRow.draft_id == draft_id)).all()
    order = {"generated": 0, "edited": 1, "returned": 2, "approved": 3}
    return sorted(rows, key=lambda e: (e.version, e.at, order[e.action]))


def _event(row: ContractDraftRow, action: str, actor: str, note: str | None = None) -> ContractDraftEventRow:
    return ContractDraftEventRow(tenant_id=row.tenant_id, draft_id=row.id, action=action, version=row.version,
                                 title=row.title, body=row.body, content_sha256=row.content_sha256, actor=actor, note=note)


def edit_draft(s: Session, p: Principal, document_id: str, draft_id: str, title: str, texts: list[str]) -> ContractDraftRow:
    """A person changes the wording. Citations stay with their paragraph, marked as edited, so a reader rechecks them."""
    require(p, PREPARER, REVIEWER, PARTNER)
    row = get_draft(s, p.tenant_id, document_id, draft_id)
    if row.status not in ("ready", "returned"):
        raise Conflict("only a finished, unapproved draft can be edited")
    title = " ".join(title.split())
    body = json.loads(row.body)
    texts = [t.strip() for t in texts]
    if not 3 <= len(title) <= 200 or len(texts) != len(body) or any(not 1 <= len(t) <= MAX_PARAGRAPH for t in texts):
        raise Invalid(f"keep a title and every paragraph, each up to {MAX_PARAGRAPH} characters")
    sha = content_hash(title, texts)
    if sha == row.content_sha256:
        return row
    for paragraph, text in zip(body, texts, strict=True):
        paragraph["edited"] = paragraph["edited"] or paragraph["text"] != text
        paragraph["text"] = text
    row.title, row.body, row.content_sha256, row.version = title, json.dumps(body), sha, row.version + 1
    row.status = "ready"
    s.add(_event(row, "edited", p.user))
    s.commit()
    return row


def decide_draft(s: Session, p: Principal, document_id: str, draft_id: str, decision: str, note: str) -> ContractDraftRow:
    require(p, REVIEWER, PARTNER)
    row = get_draft(s, p.tenant_id, document_id, draft_id)
    if row.status != "ready":
        raise Conflict("only a draft waiting for review can be decided")
    contract = get_document(s, p.tenant_id, row.document_id)
    makers = {row.requested_by, contract.uploaded_by,
              *(e.actor for e in events(s, p.tenant_id, row.id) if e.action == "edited")}
    if p.user in makers:
        raise Forbidden("maker-checker: someone who did not request, edit or upload this must decide")
    note = " ".join(note.split())
    if decision == "returned" and len(note) < 5:
        raise Invalid("say what needs to change")
    if decision == "approved":
        body = json.loads(row.body)
        if any(placeholders(text) for text in (row.title, *(x["text"] for x in body))):
            raise Conflict("fill in every [[placeholder]] before approving")
    row.status = decision
    s.add(_event(row, decision, p.user, note[:1000] or None))
    editors = {e.actor for e in events(s, p.tenant_id, row.id) if e.action == "edited"}
    if decision == "approved":
        notify(s, p.tenant_id, "draft_approved", "Your draft was approved",
               "A draft you asked for or edited was approved and is ready to copy.",
               f"/documents/{row.document_id}?tab=drafts", people={row.requested_by, *editors}, exclude=(p.user,))
    else:
        notify(s, p.tenant_id, "draft_returned", "A draft was returned with changes to make",
               "A draft you asked for or edited was returned with a note on what to change.",
               f"/documents/{row.document_id}?tab=drafts", people={row.requested_by, *editors}, exclude=(p.user,))
    s.commit()
    return row


def run_draft_job(factory: sessionmaker[Session], store: BlobStore, agent: ReviewAgent, tenant_id: str,
                  document_id: str) -> None:
    """A failure is recorded and not retried, so a bad run is paid for once. No connection is held while the agent works."""
    with tenant_session(factory, tenant_id) as s:
        row = _active(s, tenant_id, document_id)
        if row is None:
            return
        draft_id, kind, instructions, requested_by = row.id, row.kind, row.instructions, row.requested_by
        row.status, row.agent, row.error = "running", agent.name[:64], None
        s.commit()
    try:
        with tenant_session(factory, tenant_id) as s:
            documents = _documents(s, store, tenant_id, document_id)
        toolbox = DraftToolbox(documents, instructions, retriever=_retriever(factory, store, tenant_id, documents))
        agent.draft(kind, instructions, toolbox)
        if toolbox.draft is None:
            raise RuntimeError("the agent stopped before writing a draft")
    except Exception as exc:
        log.exception("contract draft %s failed", draft_id)
        with tenant_session(factory, tenant_id) as s:
            row = s.get(ContractDraftRow, draft_id)
            row.status, row.error = "failed", _failure(exc)
            s.commit()
        notify_safely(factory, tenant_id, "draft_failed", "A draft did not finish",
                      "A draft you asked for did not finish. Nothing from it was saved.",
                      f"/documents/{document_id}?tab=drafts", people=(requested_by,))
        return
    draft = toolbox.draft
    body = [{"text": x.text, "citations": [_citation(c) for c in x.citations], "edited": False} for x in draft.paragraphs]
    with tenant_session(factory, tenant_id) as s:
        row = s.get(ContractDraftRow, draft_id)
        row.title, row.body, row.open_points = draft.title, json.dumps(body), json.dumps(list(draft.open_points))
        row.content_sha256, row.version, row.status = content_hash(draft.title, [x.text for x in draft.paragraphs]), 1, "ready"
        s.add(_event(row, "generated", agent.name[:64]))
        uploader = get_document(s, tenant_id, document_id).uploaded_by
        s.commit()
    notify_safely(factory, tenant_id, "draft_ready", "A draft is waiting for review",
                  "A first draft is ready for a reviewer or partner to check and approve.",
                  f"/documents/{document_id}?tab=drafts", roles=("reviewer", "partner"),
                  exclude=(requested_by, uploader))


class DraftParagraphOut(BaseModel):
    text: str
    citations: list[CitationOut]
    edited: bool


class DraftEventOut(BaseModel):
    action: str
    version: int
    actor: str
    note: str | None
    content_sha256: str
    at: datetime


class DraftOut(BaseModel):
    id: str
    document_id: str
    kind: str
    instructions: str
    status: str
    agent: str | None
    title: str | None
    paragraphs: list[DraftParagraphOut]
    open_points: list[str]
    version: int
    content_sha256: str | None
    error: str | None
    requested_by: str
    created_at: datetime
    history: list[DraftEventOut]


class DraftIn(BaseModel):
    kind: Literal["notice", "amendment", "letter"]
    instructions: str = Field(max_length=MAX_INSTRUCTIONS)


class DraftEditIn(BaseModel):
    title: str = Field(max_length=200)
    paragraphs: list[str] = Field(max_length=40)


class DraftDecisionIn(BaseModel):
    decision: Literal["approved", "returned"]
    note: str = Field(default="", max_length=1000)


def _out(s: Session, row: ContractDraftRow) -> DraftOut:
    return DraftOut(
        id=row.id, document_id=row.document_id, kind=row.kind, instructions=row.instructions, status=row.status,
        agent=row.agent, title=row.title, paragraphs=json.loads(row.body), open_points=json.loads(row.open_points),
        version=row.version, content_sha256=row.content_sha256, error=row.error, requested_by=row.requested_by,
        created_at=row.created_at,
        history=[DraftEventOut(action=e.action, version=e.version, actor=e.actor, note=e.note,
                               content_sha256=e.content_sha256, at=e.at) for e in events(s, row.tenant_id, row.id)],
    )


def add_routes(app: FastAPI, Auth, Db) -> None:  # noqa: N803 - FastAPI dependency aliases
    @app.get("/documents/{document_id}/drafts", response_model=list[DraftOut])
    def get_drafts(document_id: str, p: Auth, s: Db) -> list[DraftOut]:
        return [_out(s, r) for r in list_drafts(s, p.tenant_id, document_id)]

    @app.post("/documents/{document_id}/drafts", response_model=DraftOut, status_code=202)
    def post_draft(document_id: str, body: DraftIn, p: Auth, s: Db) -> DraftOut:
        return _out(s, start_draft(s, p, document_id, body.kind, body.instructions))

    @app.put("/documents/{document_id}/drafts/{draft_id}", response_model=DraftOut)
    def put_draft(document_id: str, draft_id: str, body: DraftEditIn, p: Auth, s: Db) -> DraftOut:
        return _out(s, edit_draft(s, p, document_id, draft_id, body.title, body.paragraphs))

    @app.post("/documents/{document_id}/drafts/{draft_id}/decision", response_model=DraftOut)
    def post_decision(document_id: str, draft_id: str, body: DraftDecisionIn, p: Auth, s: Db) -> DraftOut:
        return _out(s, decide_draft(s, p, document_id, draft_id, body.decision, body.note))

