"""Contract review use cases. The agent proposes; a person decides; a term still goes through its normal review."""
import json
import logging
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from provenant.api.auth import PARTNER, PREPARER, REVIEWER, Principal
from provenant.api.db import (
    ContractFindingRow,
    ContractQuestionRow,
    ContractReviewRow,
    Document,
    FieldRow,
    Job,
    LegalPlaybookRow,
    tenant_session,
)
from provenant.api.services import (
    INPUT_ONLY_FIELDS,
    KINDS,
    REPLACED_COLUMNS,
    Conflict,
    Forbidden,
    Invalid,
    NotFound,
    get_document,
    list_amendments,
    require,
)
from provenant.api.storage import BlobStore
from provenant.extraction.pdftext import open_pages
from provenant.legal import PLAYBOOK_VERSION, ContractDocument, ReviewToolbox, Topic, fingerprint, topics_with
from provenant.legal.agent import ReviewAgent
from provenant.provenance import ExtractedField, SourceRef, Status

log = logging.getLogger(__name__)
REVIEW_JOB, QUESTION_JOB = "legal_review", "legal_question"
ACTIVE = ("queued", "running")


def _custom_positions(s: Session, tenant_id: str) -> tuple[dict[str, str], LegalPlaybookRow | None]:
    row = s.scalar(select(LegalPlaybookRow).where(LegalPlaybookRow.tenant_id == tenant_id))
    return (json.loads(row.positions) if row else {}), row


def playbook(s: Session, tenant_id: str) -> tuple[tuple[Topic, ...], LegalPlaybookRow | None]:
    custom, row = _custom_positions(s, tenant_id)
    return topics_with(custom), row


def save_playbook(s: Session, p: Principal, positions: dict[str, str]) -> tuple[Topic, ...]:
    """A partner sets the firm's positions. Reviews already run keep the positions they used."""
    require(p, PARTNER)
    try:
        topics = topics_with(positions)
    except ValueError as exc:
        raise Invalid(str(exc)) from exc
    _, row = _custom_positions(s, p.tenant_id)
    body = json.dumps({key: next(t.position for t in topics if t.key == key) for key in positions}, sort_keys=True)
    if row is None:
        row = LegalPlaybookRow(tenant_id=p.tenant_id, positions=body, updated_by=p.user)
        s.add(row)
    row.positions, row.updated_by, row.updated_at = body, p.user, datetime.now(UTC)
    s.commit()
    return topics


def _contract(s: Session, tenant_id: str, document_id: str) -> Document:
    doc = get_document(s, tenant_id, document_id)
    if doc.kind == "amendment":
        raise Conflict("review the contract itself; its amendments are read with it")
    return doc


def _active(s: Session, tenant_id: str, document_id: str) -> ContractReviewRow | None:
    return s.scalar(select(ContractReviewRow).where(
        ContractReviewRow.tenant_id == tenant_id, ContractReviewRow.document_id == document_id,
        ContractReviewRow.status.in_(ACTIVE)).order_by(ContractReviewRow.created_at).limit(1))


def start_review(s: Session, p: Principal, document_id: str) -> ContractReviewRow:
    require(p, PREPARER, REVIEWER, PARTNER)
    doc = _contract(s, p.tenant_id, document_id)
    family = [doc, *list_amendments(s, p.tenant_id, doc.id)]
    if any(d.status != "extracted" for d in family):
        raise Conflict("wait until the contract and its amendments have been read")
    if _active(s, p.tenant_id, doc.id):
        raise Conflict("a review of this contract is already running")
    topics, _ = playbook(s, p.tenant_id)
    row = ContractReviewRow(
        tenant_id=p.tenant_id, document_id=doc.id, status="queued", playbook_version=PLAYBOOK_VERSION,
        playbook_sha256=fingerprint(topics), playbook=json.dumps({t.key: t.position for t in topics}),
        requested_by=p.user,
    )
    s.add(row)
    s.add(Job(tenant_id=p.tenant_id, kind=REVIEW_JOB, document_id=doc.id))  # commits with the review (outbox)
    s.commit()
    return row


def list_reviews(s: Session, tenant_id: str, document_id: str) -> list[ContractReviewRow]:
    doc = _contract(s, tenant_id, document_id)
    return list(s.scalars(select(ContractReviewRow).where(
        ContractReviewRow.tenant_id == tenant_id, ContractReviewRow.document_id == doc.id,
    ).order_by(ContractReviewRow.created_at.desc())))


def get_review(s: Session, tenant_id: str, document_id: str, review_id: str) -> ContractReviewRow:
    row = s.scalar(select(ContractReviewRow).where(
        ContractReviewRow.tenant_id == tenant_id, ContractReviewRow.document_id == document_id,
        ContractReviewRow.id == review_id))
    if row is None:
        raise NotFound("review not found")
    return row


def findings(s: Session, tenant_id: str, review_id: str) -> list[ContractFindingRow]:
    rank = {"high": 0, "medium": 1, "low": 2, "info": 3}
    rows = s.scalars(select(ContractFindingRow).where(
        ContractFindingRow.tenant_id == tenant_id, ContractFindingRow.review_id == review_id)).all()
    return sorted(rows, key=lambda r: (rank[r.severity], r.filename, r.page, r.top))


def questions(s: Session, tenant_id: str, review_id: str) -> list[ContractQuestionRow]:
    return list(s.scalars(select(ContractQuestionRow).where(
        ContractQuestionRow.tenant_id == tenant_id, ContractQuestionRow.review_id == review_id,
    ).order_by(ContractQuestionRow.created_at)))


def ask_question(s: Session, p: Principal, document_id: str, review_id: str, question: str) -> ContractQuestionRow:
    require(p, PREPARER, REVIEWER, PARTNER)
    review = get_review(s, p.tenant_id, document_id, review_id)
    if review.status != "done":
        raise Conflict("ask once the review has finished")
    question = " ".join(question.split())
    if not 5 <= len(question) <= 1000:
        raise Invalid("a question must be 5 to 1000 characters")
    row = ContractQuestionRow(tenant_id=p.tenant_id, review_id=review.id, document_id=review.document_id,
                              question=question, status="queued", asked_by=p.user)
    s.add(row)
    s.add(Job(tenant_id=p.tenant_id, kind=QUESTION_JOB, document_id=review.document_id))
    s.commit()
    return row


def decide_finding(s: Session, p: Principal, document_id: str, review_id: str, finding_id: str, decision: str,
                   note: str) -> ContractFindingRow:
    """Accepting a finding with a proposed term puts that term up for its normal review; it never verifies it."""
    require(p, REVIEWER, PARTNER)
    review = get_review(s, p.tenant_id, document_id, review_id)
    row = s.scalar(select(ContractFindingRow).where(
        ContractFindingRow.tenant_id == p.tenant_id, ContractFindingRow.review_id == review.id,
        ContractFindingRow.id == finding_id))
    if row is None:
        raise NotFound("finding not found")
    contract = get_document(s, p.tenant_id, review.document_id)
    source_doc = get_document(s, p.tenant_id, row.document_id)
    if p.user in (contract.uploaded_by, source_doc.uploaded_by):
        raise Forbidden("maker-checker: someone other than the person who uploaded the contract must decide")
    if row.decision:
        raise Conflict(f"already {row.decision} by {row.decided_by}")
    note = " ".join(note.split())
    if decision == "rejected" and len(note) < 5:
        raise Invalid("say why the finding is rejected")
    if decision == "accepted" and row.proposed_term:
        row.field_id = _propose_term(s, p, source_doc, row)
    row.decision, row.decision_note, row.decided_by, row.decided_at = decision, note[:1000] or None, p.user, datetime.now(UTC)
    s.commit()
    return row


def _propose_term(s: Session, p: Principal, doc: Document, finding: ContractFindingRow) -> str | None:
    """The term's id, or None when the contract already has a live value, which a finding never overwrites."""
    if finding.proposed_term not in KINDS[doc.kind].fields or finding.proposed_term in INPUT_ONLY_FIELDS:
        raise Conflict(f"{finding.proposed_term} is not a term of this document")
    existing = s.scalar(select(FieldRow).where(FieldRow.tenant_id == p.tenant_id, FieldRow.document_id == doc.id,
                                               FieldRow.name == finding.proposed_term))
    if existing is not None and existing.status != Status.REJECTED.value:
        return None
    source = SourceRef(finding.document_sha256, finding.page, (finding.x0, finding.top, finding.x1, finding.bottom),
                       finding.char_start, finding.char_end)
    entered = FieldRow.from_domain(p.tenant_id, doc.id, ExtractedField(
        finding.proposed_term, finding.proposed_value, 0.9, source, "legal-agent", Status.NEEDS_REVIEW,
        gate_version="legal-agent"))
    entered.entered_by = p.user
    if existing is None:
        s.add(entered)
        s.flush()
        return entered.id
    for column in REPLACED_COLUMNS:
        setattr(existing, column, getattr(entered, column))
    return existing.id


def _documents(s: Session, store: BlobStore, tenant_id: str, document_id: str) -> list[ContractDocument]:
    doc = get_document(s, tenant_id, document_id)
    out = []
    for d in (doc, *list_amendments(s, tenant_id, doc.id)):
        with open_pages(store.get(d.sha256)) as pages:
            read = tuple(pages)
        terms = frozenset(KINDS[d.kind].fields) - INPUT_ONLY_FIELDS
        out.append(ContractDocument(d.id, d.filename, d.kind, d.sha256, read, terms))
    if not any(page.text.strip() for d in out for page in d.pages):
        raise ValueError("no readable text: scanned PDFs need OCR before they can be reviewed")
    return out


def _stored_topics(review: ContractReviewRow) -> tuple[Topic, ...]:
    return topics_with(json.loads(review.playbook))


def _citation(c) -> dict:
    x0, top, x1, bottom = c.source.bbox
    return {"document_id": c.document_id, "filename": c.filename, "quote": c.quote, "page": c.source.page,
            "bbox": [x0, top, x1, bottom]}


def _failure(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"[:500]


def run_review_job(factory: sessionmaker[Session], store: BlobStore, agent: ReviewAgent, tenant_id: str,
                   document_id: str) -> None:
    """A failure is recorded on the review and not retried automatically, so a bad run is not paid for three times.
    The database connection is released while the agent works, which can take minutes."""
    with tenant_session(factory, tenant_id) as s:
        review = _active(s, tenant_id, document_id)
        if review is None:
            return
        review_id = review.id
        review.status, review.agent, review.error = "running", agent.name[:64], None
        s.execute(delete(ContractFindingRow).where(ContractFindingRow.tenant_id == tenant_id,
                                                   ContractFindingRow.review_id == review_id))  # a reclaimed job
        s.commit()
        topics = _stored_topics(review)
    try:
        with tenant_session(factory, tenant_id) as s:
            documents = _documents(s, store, tenant_id, document_id)
        toolbox = ReviewToolbox(documents, topics)
        agent.review(topics, toolbox)
        if not toolbox.finished:
            raise RuntimeError("the agent stopped before accounting for every topic")
    except Exception as exc:
        log.exception("contract review %s failed", review_id)
        with tenant_session(factory, tenant_id) as s:
            review = s.get(ContractReviewRow, review_id)
            review.status, review.error, review.finished_at = "failed", _failure(exc), datetime.now(UTC)
            s.commit()
        return
    with tenant_session(factory, tenant_id) as s:
        for f in toolbox.findings:
            x0, top, x1, bottom = f.citation.source.bbox
            s.add(ContractFindingRow(
                tenant_id=tenant_id, review_id=review_id, document_id=f.citation.document_id,
                filename=f.citation.filename[:255], topic=f.topic, severity=f.severity, title=f.title,
                explanation=f.explanation, suggestion=f.suggestion, quote=f.citation.quote,
                document_sha256=f.citation.source.document_sha256, page=f.citation.source.page, x0=x0, top=top, x1=x1,
                bottom=bottom, char_start=f.citation.source.char_start, char_end=f.citation.source.char_end,
                proposed_term=f.proposed_term, proposed_value=f.proposed_value,
            ))
        review = s.get(ContractReviewRow, review_id)
        review.status, review.summary, review.not_found = "done", toolbox.summary, json.dumps(list(toolbox.not_found))
        review.finished_at = datetime.now(UTC)
        s.commit()


def run_question_job(factory: sessionmaker[Session], store: BlobStore, agent: ReviewAgent, tenant_id: str,
                     document_id: str) -> None:
    with tenant_session(factory, tenant_id) as s:
        row = s.scalar(select(ContractQuestionRow).where(
            ContractQuestionRow.tenant_id == tenant_id, ContractQuestionRow.document_id == document_id,
            ContractQuestionRow.status.in_(ACTIVE)).order_by(ContractQuestionRow.created_at).limit(1))
        if row is None:
            return
        question_id, question = row.id, row.question
        row.status, row.error = "running", None
        topics = _stored_topics(s.get(ContractReviewRow, row.review_id))
        s.commit()
    try:
        with tenant_session(factory, tenant_id) as s:
            documents = _documents(s, store, tenant_id, document_id)
        toolbox = ReviewToolbox(documents, topics, mode="question")
        agent.ask(question, topics, toolbox)
        if toolbox.answer is None:
            raise RuntimeError("the agent stopped before answering")
    except Exception as exc:
        log.exception("contract question %s failed", question_id)
        with tenant_session(factory, tenant_id) as s:
            row = s.get(ContractQuestionRow, question_id)
            row.status, row.error = "failed", _failure(exc)
            s.commit()
        return
    with tenant_session(factory, tenant_id) as s:
        row = s.get(ContractQuestionRow, question_id)
        row.status, row.answer, row.not_stated = "done", toolbox.answer.text, toolbox.answer.not_stated
        row.citations = json.dumps([_citation(c) for c in toolbox.answer.citations])
        s.commit()
