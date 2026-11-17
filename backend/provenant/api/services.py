"""Use cases. Routes stay thin; every query here is scoped by tenant_id explicitly (RLS is the backstop)."""
import hashlib
import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from provenant.api.db import Document, FieldRow, ReviewEventRow, tenant_session
from provenant.api.storage import BlobStore
from provenant.extraction import Extractor
from provenant.leases import LeaseSchedule, LeaseTerms, build_schedule
from provenant.provenance import GENESIS, ConfidenceGate, Status, review, verify_chain

log = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
# Rates and payment timing are judgement calls (IBR, in advance vs arrears): a human always signs off.
LEASE_GATE = ConfidenceGate("lease-v1", threshold=0.95, always_review=frozenset({"annual_discount_rate", "payment_timing"}))


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


class Invalid(Exception):
    pass


def get_document(s: Session, tenant_id: str, document_id: str) -> Document:
    doc = s.scalar(select(Document).where(Document.tenant_id == tenant_id, Document.id == document_id))
    if doc is None:
        raise NotFound("document not found")
    return doc


def list_fields(s: Session, tenant_id: str, document_id: str) -> list[FieldRow]:
    return list(
        s.scalars(
            select(FieldRow)
            .where(FieldRow.tenant_id == tenant_id, FieldRow.document_id == document_id)
            .order_by(FieldRow.name)
        )
    )


def upload_document(s: Session, store: BlobStore, tenant_id: str, filename: str, data: bytes) -> tuple[Document, bool]:
    """Idempotent by content: the same PDF uploaded twice returns the same document."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise Invalid(f"file exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    if not data.startswith(b"%PDF-"):
        raise Invalid("only PDF files are accepted")
    sha = hashlib.sha256(data).hexdigest()
    query = select(Document).where(Document.tenant_id == tenant_id, Document.sha256 == sha)
    if existing := s.scalar(query):
        return existing, False

    store.put(sha, data)
    doc = Document(tenant_id=tenant_id, sha256=sha, filename=filename[:255] or "upload.pdf", size_bytes=len(data))
    s.add(doc)
    try:
        s.commit()
    except IntegrityError:  # the same file arrived concurrently
        s.rollback()
        return s.scalars(query).one(), False
    return doc, True


def run_extraction(
    factory: sessionmaker[Session], store: BlobStore, extractor: Extractor, tenant_id: str, document_id: str
) -> None:
    """shortcut: runs in-process as a FastAPI background task; move to an outbox + worker when extraction calls a model."""
    with tenant_session(factory, tenant_id) as s:
        doc = get_document(s, tenant_id, document_id)
        if doc.status not in ("uploaded", "failed"):
            return
        doc.status, doc.error = "extracting", None
        s.commit()
        try:
            proposals = extractor.extract(store.get(doc.sha256), doc.sha256)
            for proposal in proposals:
                s.add(FieldRow.from_domain(tenant_id, doc.id, LEASE_GATE.route(proposal)))
            doc.status = "extracted"
            s.commit()
        except Exception as exc:
            log.exception("extraction failed for document %s", document_id)
            s.rollback()
            doc = get_document(s, tenant_id, document_id)
            doc.status, doc.error = "failed", f"{type(exc).__name__}: {exc}"[:500]
            s.commit()


def review_queue(s: Session, tenant_id: str) -> list[FieldRow]:
    return list(
        s.scalars(
            select(FieldRow)
            .where(FieldRow.tenant_id == tenant_id, FieldRow.status == Status.NEEDS_REVIEW.value)
            .order_by(FieldRow.updated_at)
        )
    )


def review_field(
    s: Session, tenant_id: str, reviewer: str, field_id: str, action: Status, corrected_value: str | None
) -> FieldRow:
    row = s.scalar(select(FieldRow).where(FieldRow.tenant_id == tenant_id, FieldRow.id == field_id))
    if row is None:
        raise NotFound("field not found")
    last = s.scalar(
        select(ReviewEventRow.hash).where(ReviewEventRow.tenant_id == tenant_id).order_by(ReviewEventRow.id.desc()).limit(1)
    )
    try:
        field, event = review(row.to_domain(), reviewer, action, corrected_value, prev_hash=last or GENESIS)
    except ValueError as exc:
        raise Conflict(str(exc)) from exc

    row.value, row.status = field.value, field.status.value
    s.add(
        ReviewEventRow(
            tenant_id=tenant_id,
            field_id=row.id,
            field_name=event.field_name,
            document_sha256=event.document_sha256,
            reviewer=event.reviewer,
            action=event.action.value,
            before=event.before,
            after=event.after,
            at=event.at,
            prev_hash=event.prev_hash,
            hash=event.hash,
        )
    )
    try:
        s.commit()
    except IntegrityError as exc:
        s.rollback()
        raise Conflict("another review was recorded at the same moment; retry") from exc
    return row


def review_log_is_intact(s: Session, tenant_id: str) -> tuple[bool, int]:
    rows = s.scalars(
        select(ReviewEventRow).where(ReviewEventRow.tenant_id == tenant_id).order_by(ReviewEventRow.id)
    ).all()
    return verify_chain(r.to_domain() for r in rows), len(rows)


def lease_schedule(s: Session, tenant_id: str, document_id: str) -> tuple[LeaseSchedule, list[FieldRow]]:
    get_document(s, tenant_id, document_id)
    rows = list_fields(s, tenant_id, document_id)
    try:
        terms = LeaseTerms.from_fields({r.name: r.to_domain() for r in rows})
    except (PermissionError, ValueError) as exc:  # unverified, missing or unparseable: a reviewer must act
        raise Conflict(str(exc)) from exc
    try:
        return build_schedule(terms), rows
    except ValueError as exc:
        raise Invalid(str(exc)) from exc
