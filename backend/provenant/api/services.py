"""Use cases. Routes stay thin; every query here is scoped by tenant_id explicitly (RLS is the backstop)."""
import hashlib
import logging

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from provenant.api.auth import PARTNER, PREPARER, REVIEWER, Principal
from provenant.api.db import Document, FieldRow, Job, ReviewEventRow, tenant_session
from provenant.api.storage import BlobStore
from provenant.extraction import Extractor
from provenant.extraction.pdftext import open_pages
from provenant.leases import LeaseSchedule, LeaseTerms, build_schedule
from provenant.provenance import GENESIS, ConfidenceGate, ExtractedField, Status, review, verify_chain

log = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
# Rates and payment timing are judgement calls (IBR, in advance vs arrears): a partner always signs off.
LEASE_GATE = ConfidenceGate("lease-v1", threshold=0.95, always_review=frozenset({"annual_discount_rate", "payment_timing"}))
ANY_ROLE = (PREPARER, REVIEWER, PARTNER)


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


class Invalid(Exception):
    pass


class Forbidden(Exception):
    pass


def require(p: Principal, *roles: str) -> None:
    if not p.has_any(*roles):
        raise Forbidden(f"requires one of: {', '.join(roles)}")


def get_document(s: Session, tenant_id: str, document_id: str) -> Document:
    doc = s.scalar(select(Document).where(Document.tenant_id == tenant_id, Document.id == document_id))
    if doc is None:
        raise NotFound("document not found")
    return doc


def list_documents(s: Session, tenant_id: str, limit: int = 200) -> list[tuple[Document, int, int]]:
    """Newest first, each with how many fields await review and how many required terms are still missing."""

    def count(*conditions):
        return select(func.count(FieldRow.id)).where(FieldRow.document_id == Document.id, *conditions).scalar_subquery()

    pending = count(FieldRow.status == Status.NEEDS_REVIEW.value)
    usable = count(FieldRow.status != Status.REJECTED.value, FieldRow.name.in_(LeaseTerms.REQUIRED))
    rows = s.execute(
        select(Document, pending, usable)
        .where(Document.tenant_id == tenant_id)
        .order_by(Document.created_at.desc())
        .limit(limit)
    )
    return [(doc, review, len(LeaseTerms.REQUIRED) - found) for doc, review, found in rows]


def list_fields(s: Session, tenant_id: str, document_id: str) -> list[FieldRow]:
    return list(
        s.scalars(
            select(FieldRow)
            .where(FieldRow.tenant_id == tenant_id, FieldRow.document_id == document_id)
            .order_by(FieldRow.name)
        )
    )


def _enqueue_extraction(s: Session, doc: Document) -> None:
    doc.status, doc.error = "queued", None
    s.add(Job(tenant_id=doc.tenant_id, kind="extract", document_id=doc.id))


def upload_document(s: Session, store: BlobStore, p: Principal, filename: str, data: bytes) -> tuple[Document, bool]:
    """Idempotent by content. The document and its extraction job commit together (transactional outbox)."""
    require(p, *ANY_ROLE)
    if len(data) > MAX_UPLOAD_BYTES:
        raise Invalid(f"file exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    if not data.startswith(b"%PDF-"):
        raise Invalid("only PDF files are accepted")
    sha = hashlib.sha256(data).hexdigest()
    query = select(Document).where(Document.tenant_id == p.tenant_id, Document.sha256 == sha)
    if existing := s.scalar(query):
        if existing.status == "failed":  # re-uploading a failed document retries it
            _enqueue_extraction(s, existing)
            s.commit()
        return existing, False

    store.put(sha, data)
    doc = Document(
        tenant_id=p.tenant_id, sha256=sha, filename=filename[:255] or "upload.pdf", size_bytes=len(data), uploaded_by=p.user
    )
    s.add(doc)
    try:
        s.flush()  # assigns doc.id; also where a concurrent duplicate surfaces
        _enqueue_extraction(s, doc)
        s.commit()
    except IntegrityError:  # the same file arrived concurrently
        s.rollback()
        return s.scalars(query).one(), False
    return doc, True


def extract_document(
    factory: sessionmaker[Session], store: BlobStore, extractor: Extractor, tenant_id: str, document_id: str
) -> None:
    """Run by the worker. Idempotent: a redelivered job for an extracted document does nothing. Raises on failure."""
    with tenant_session(factory, tenant_id) as s:
        doc = get_document(s, tenant_id, document_id)
        if doc.status == "extracted":
            return
        doc.status, doc.error = "extracting", None
        s.commit()
        try:
            for proposal in extractor.extract(store.get(doc.sha256), doc.sha256):
                s.add(FieldRow.from_domain(tenant_id, doc.id, LEASE_GATE.route(proposal)))
            doc.status = "extracted"
            s.commit()
        except Exception as exc:
            s.rollback()
            doc = get_document(s, tenant_id, document_id)
            doc.status, doc.error = "failed", f"{type(exc).__name__}: {exc}"[:500]
            s.commit()
            raise


def review_queue(s: Session, tenant_id: str) -> list[FieldRow]:
    return list(
        s.scalars(
            select(FieldRow)
            .where(FieldRow.tenant_id == tenant_id, FieldRow.status == Status.NEEDS_REVIEW.value)
            .order_by(FieldRow.updated_at)
        )
    )


def review_field(
    s: Session, p: Principal, field_id: str, action: Status, corrected_value: str | None
) -> FieldRow:
    require(p, REVIEWER, PARTNER)
    row = s.scalar(select(FieldRow).where(FieldRow.tenant_id == p.tenant_id, FieldRow.id == field_id))
    if row is None:
        raise NotFound("field not found")
    if row.name in LEASE_GATE.always_review:
        require(p, PARTNER)
    doc = get_document(s, p.tenant_id, row.document_id)
    if p.user in (doc.uploaded_by, row.entered_by):
        raise Forbidden("maker-checker: you cannot review a figure you uploaded or entered")
    if corrected_value is not None:
        try:
            corrected_value = LeaseTerms.check_value(row.name, corrected_value)
        except ValueError as exc:
            raise Invalid(str(exc)) from exc

    last = s.scalar(
        select(ReviewEventRow.hash)
        .where(ReviewEventRow.tenant_id == p.tenant_id)
        .order_by(ReviewEventRow.id.desc())
        .limit(1)
    )
    try:
        field, event = review(row.to_domain(), p.user, action, corrected_value, prev_hash=last or GENESIS)
    except ValueError as exc:
        raise Conflict(str(exc)) from exc

    row.value, row.status = field.value, field.status.value
    s.add(
        ReviewEventRow(
            tenant_id=p.tenant_id,
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


def enter_field(
    s: Session,
    store: BlobStore,
    p: Principal,
    document_id: str,
    name: str,
    value: str,
    page: int,
    bbox: tuple[float, float, float, float],
) -> FieldRow:
    """A human-entered value. It must point at printed text in the document, and someone else must review it."""
    require(p, *ANY_ROLE)
    try:
        value = LeaseTerms.check_value(name, value)
    except ValueError as exc:
        raise Invalid(str(exc)) from exc
    doc = get_document(s, p.tenant_id, document_id)
    if doc.status != "extracted":
        raise Conflict("wait for extraction to finish before entering fields")
    row = s.scalar(select(FieldRow).where(FieldRow.document_id == doc.id, FieldRow.name == name))
    if row is not None and row.status != Status.REJECTED.value:
        raise Conflict(f"{name} already exists ({row.status}); reject it before entering a replacement")

    with open_pages(store.get(doc.sha256)) as pages:
        if not 1 <= page <= len(pages):
            raise Invalid(f"page must be between 1 and {len(pages)}")
        span = pages[page - 1].span_in_box(bbox)
        source = span and pages[page - 1].source(doc.sha256, *span)  # tightened to the printed characters
    if not source:
        raise Invalid("no text inside that box; scanned pages need OCR before values can be cited")

    entered = FieldRow.from_domain(
        p.tenant_id,
        doc.id,
        ExtractedField(name, value, 1.0, source, "manual", Status.NEEDS_REVIEW, gate_version="manual"),
    )
    entered.entered_by = p.user
    if row is None:
        s.add(entered)
        row = entered
    else:  # replace the rejected proposal; the review log keeps its history
        for column in ("value", "confidence", "page", "x0", "top", "x1", "bottom", "char_start", "char_end",
                       "extractor", "entered_by", "status", "gate_version"):
            setattr(row, column, getattr(entered, column))
    s.commit()
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
