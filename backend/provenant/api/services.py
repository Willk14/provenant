"""Use cases. Routes stay thin; every query here is scoped by tenant_id explicitly (RLS is the backstop)."""
import hashlib
import logging
from dataclasses import dataclass, field

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from provenant.core import spreadsheet
from provenant.api.auth import PARTNER, PREPARER, REVIEWER, Principal
from provenant.api.db import Document, FieldRow, Job, ReviewEventRow, tenant_session
from provenant.api.storage import BlobStore
from provenant.covenants import Terms as CovenantTerms
from provenant.extraction import Extractor
from provenant.extraction.pdftext import open_pages
from provenant.leases import LeaseSchedule, LeaseTerms, Modification, build_schedule, check_value, expansion_lease
from provenant.leases.ifrs16 import SEPARATE_LEASE
from provenant.provenance import GENESIS, ConfidenceGate, DocumentRef, ExtractedField, Status, review, verify_chain

log = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
# Rates and payment timing are judgement calls (IBR, in advance vs arrears): a partner always signs off.
LEASE_GATE = ConfidenceGate("lease-v1", threshold=0.95, always_review=frozenset({"annual_discount_rate", "payment_timing"}))
# When an amendment takes effect decides which periods it changes, so a partner signs that off too.
AMENDMENT_GATE = ConfidenceGate(
    "amendment-v1", threshold=0.95,
    always_review=frozenset({"effective_date", "annual_discount_rate", "expansion_treatment"}),
)


@dataclass(frozen=True)
class Kind:
    fields: tuple[str, ...]  # terms kept from extraction for this kind of document
    required: tuple[str, ...]
    gate: ConfidenceGate


# Covenant thresholds and the cure period are where the money and the legal risk sit: a partner signs them off.
COVENANT_GATE = ConfidenceGate(
    "covenant-v1",
    threshold=0.95,
    always_review=frozenset({"max_leverage_ratio", "min_interest_cover", "min_liquidity", "max_leverage_steps",
                             "min_interest_cover_steps", "min_liquidity_steps", "cure_period_days", "cure_day_basis",
                             "cure_runs_from", "springing_usage_percent", "revolver_commitment",
                             "addback_cap_percent", "addback_cap_basis", "lease_treatment"}),
)

KINDS = {
    "lease": Kind(LeaseTerms.FIELDS, LeaseTerms.REQUIRED, LEASE_GATE),
    "amendment": Kind(Modification.FIELDS, Modification.REQUIRED, AMENDMENT_GATE),
    "credit_agreement": Kind(CovenantTerms.FIELDS, CovenantTerms.REQUIRED, COVENANT_GATE),
}
UPLOAD_KINDS = ("lease", "credit_agreement")  # amendments are uploaded with `amends`
ANY_ROLE = (PREPARER, REVIEWER, PARTNER)


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


class Invalid(Exception):
    pass


class Forbidden(Exception):
    pass


def as_csv(data: bytes, filename: str) -> bytes:
    """An uploaded table as CSV: an Excel workbook's first sheet is converted, CSV passes through (ADR 0010)."""
    try:
        return spreadsheet.to_csv(data, filename)
    except spreadsheet.SpreadsheetError as exc:
        raise Invalid(str(exc)) from exc


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
    is_required = or_(*(and_(Document.kind == k, FieldRow.name.in_(spec.required)) for k, spec in KINDS.items()))
    usable = count(FieldRow.status != Status.REJECTED.value, is_required)
    rows = s.execute(
        select(Document, pending, usable)
        .where(Document.tenant_id == tenant_id)
        .order_by(Document.created_at.desc())
        .limit(limit)
    )
    return [(doc, review, len(KINDS[doc.kind].required) - found) for doc, review, found in rows]


def list_amendments(s: Session, tenant_id: str, lease_id: str) -> list[Document]:
    return list(
        s.scalars(
            select(Document)
            .where(Document.tenant_id == tenant_id, Document.parent_id == lease_id)
            .order_by(Document.created_at)
        )
    )


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


def upload_document(
    s: Session,
    store: BlobStore,
    p: Principal,
    filename: str,
    data: bytes,
    amends: str | None = None,
    kind: str = "lease",
) -> tuple[Document, bool]:
    """Idempotent by content. The document and its extraction job commit together (transactional outbox).

    With `amends`, the file is an amendment (addendum, side letter, extension) to that lease.
    """
    require(p, *ANY_ROLE)
    if kind not in UPLOAD_KINDS:
        raise Invalid(f"kind must be one of {', '.join(UPLOAD_KINDS)}")
    if amends and kind != "lease":
        raise Invalid("only leases have amendments here")
    if len(data) > MAX_UPLOAD_BYTES:
        raise Invalid(f"file exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    if not data.startswith(b"%PDF-"):
        raise Invalid("only PDF files are accepted")
    lease = None
    if amends:
        lease = get_document(s, p.tenant_id, amends)
        if lease.kind != "lease":
            raise Invalid("amend the lease itself, not another amendment; the chain is ordered by effective date")
    sha = hashlib.sha256(data).hexdigest()
    query = select(Document).where(Document.tenant_id == p.tenant_id, Document.sha256 == sha)
    wanted_kind = "amendment" if lease else kind
    if existing := s.scalar(query):
        if existing.parent_id != (lease.id if lease else None) or existing.kind != wanted_kind:
            where = {"amendment": "as an amendment", "lease": "as a lease", "credit_agreement": "as a credit agreement"}
            raise Conflict(f"this file is already uploaded {where[existing.kind]} ({existing.filename})")
        if existing.status == "failed":  # re-uploading a failed document retries it
            _enqueue_extraction(s, existing)
            s.commit()
        return existing, False
    if lease and sha == lease.sha256:
        raise Conflict("a document cannot amend itself")

    store.put(sha, data)
    doc = Document(
        tenant_id=p.tenant_id,
        sha256=sha,
        filename=filename[:255] or "upload.pdf",
        size_bytes=len(data),
        uploaded_by=p.user,
        kind=wanted_kind,
        parent_id=lease.id if lease else None,
        parent_sha256=lease.sha256 if lease else None,
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
        kind = KINDS[doc.kind]
        try:
            for proposal in extractor.extract(store.get(doc.sha256), doc.sha256, kind=doc.kind):
                if proposal.name in kind.fields:  # e.g. a lease's "effective date" wording is not a term here
                    s.add(FieldRow.from_domain(tenant_id, doc.id, kind.gate.route(proposal)))
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
    doc = get_document(s, p.tenant_id, row.document_id)
    if row.name in KINDS[doc.kind].gate.always_review:
        require(p, PARTNER)
    if p.user in (doc.uploaded_by, row.entered_by):
        raise Forbidden("maker-checker: you cannot review a figure you uploaded or entered")
    if corrected_value is not None:
        try:
            corrected_value = check_value(row.name, corrected_value)
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
    doc = get_document(s, p.tenant_id, document_id)
    if name not in KINDS[doc.kind].fields:
        raise Invalid(f"{name} is not a term of a {doc.kind}")
    if name in INPUT_ONLY_FIELDS:
        raise Invalid(f"{name} is a preparer's judgement, not printed in the contract: enter it as an input with its basis")
    try:
        value = check_value(name, value)
    except ValueError as exc:
        raise Invalid(str(exc)) from exc
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
        for column in REPLACED_COLUMNS:
            setattr(row, column, getattr(entered, column))
    s.commit()
    return row


REPLACED_COLUMNS = ("value", "confidence", "page", "x0", "top", "x1", "bottom", "char_start", "char_end",
                    "extractor", "entered_by", "status", "gate_version", "basis")
# Terms that contracts rarely state because they are the preparer's own estimate (ADR 0005 finding 1).
INPUT_FIELDS = frozenset({"annual_discount_rate", "expansion_treatment", "cure_day_basis", "cure_runs_from",
                          "springing_usage_percent", "revolver_commitment", "addback_cap_percent",
                          "addback_cap_basis", "lease_treatment"})
# Judgements no contract states, so they are never marked on the PDF (the IFRS 16.44 separate-lease test).
INPUT_ONLY_FIELDS = frozenset({"expansion_treatment"})


def enter_input(s: Session, p: Principal, document_id: str, name: str, value: str, basis: str) -> FieldRow:
    """A value the document does not state, entered with its basis. It cites no page; review is as for any
    judgement term (a partner signs it off, and never the person who entered it)."""
    require(p, *ANY_ROLE)
    doc = get_document(s, p.tenant_id, document_id)
    if name not in INPUT_FIELDS or name not in KINDS[doc.kind].fields:
        raise Invalid(f"{name} cannot be entered as a preparer input for a {doc.kind}; mark where the document states it")
    basis = " ".join(basis.split())
    if len(basis) < 10:
        raise Invalid("state the basis for the input (e.g. how the incremental borrowing rate was determined)")
    try:
        value = check_value(name, value)
    except ValueError as exc:
        raise Invalid(str(exc)) from exc
    if doc.status != "extracted":
        raise Conflict("wait for extraction to finish before entering fields")
    row = s.scalar(select(FieldRow).where(FieldRow.document_id == doc.id, FieldRow.name == name))
    if row is not None and row.status != Status.REJECTED.value:
        raise Conflict(f"{name} already exists ({row.status}); reject it before entering a replacement")
    entered = FieldRow.from_domain(p.tenant_id, doc.id, ExtractedField(
        name, value, 1.0, DocumentRef(doc.sha256), "preparer_input", Status.NEEDS_REVIEW,
        gate_version="manual", basis=basis[:1000],
    ))
    entered.entered_by = p.user
    if row is None:
        s.add(entered)
        row = entered
    else:  # replace the rejected proposal; the review log keeps its history
        for column in REPLACED_COLUMNS:
            setattr(row, column, getattr(entered, column))
    s.commit()
    return row


def review_log_is_intact(s: Session, tenant_id: str) -> tuple[bool, int]:
    rows = s.scalars(
        select(ReviewEventRow).where(ReviewEventRow.tenant_id == tenant_id).order_by(ReviewEventRow.id)
    ).all()
    return verify_chain(r.to_domain() for r in rows), len(rows)


@dataclass(frozen=True)
class Segment:
    """Which document's terms govern a run of periods, and which field supplied each term."""

    document_id: str
    from_period: int
    inputs: dict[str, str]  # term name -> field id


@dataclass(frozen=True)
class ChainedSchedule:
    schedule: LeaseSchedule
    lease: Document
    amendments: list[Document]  # in effective-date order, matching schedule.remeasurements
    fields: list[FieldRow]  # every input across the chain
    segments: list[Segment]
    fingerprint: str  # SHA-256 over the lease and each amendment's file hash and effective date
    separate: list["SeparateLease"] = field(default_factory=list)  # expansions judged separate leases


@dataclass(frozen=True)
class SeparateLease:
    """Added space accounted for as its own lease (IFRS 16.44), from an expansion amendment."""

    amendment: Document
    schedule: LeaseSchedule
    fields: list[FieldRow]


def lease_schedule(s: Session, tenant_id: str, document_id: str) -> ChainedSchedule:
    """The lease's schedule with every amendment applied in effective-date order. Any amendment that is
    unread, unverified or ambiguous blocks the whole schedule: a superseded term must never feed a figure."""
    lease = get_document(s, tenant_id, document_id)
    if lease.kind == "amendment":
        raise Conflict("this is an amendment; its effect is on the schedule of the lease it amends")
    if lease.kind != "lease":
        raise Conflict("only leases have a lease schedule")
    lease_rows = list_fields(s, tenant_id, lease.id)
    try:
        terms = LeaseTerms.from_fields({r.name: r.to_domain() for r in lease_rows})
    except (PermissionError, ValueError) as exc:  # unverified, missing or unparseable: a reviewer must act
        raise Conflict(str(exc)) from exc

    chain: list[tuple[Modification, Document, list[FieldRow]]] = []
    for amendment in list_amendments(s, tenant_id, lease.id):
        if amendment.parent_sha256 != lease.sha256:
            raise Conflict(f"{amendment.filename} does not chain to this lease file (hash mismatch)")
        if amendment.status != "extracted":
            state = "could not be read; upload it again" if amendment.status == "failed" else "is still being read"
            raise Conflict(f"amendment {amendment.filename} {state}")
        rows = list_fields(s, tenant_id, amendment.id)
        try:
            chain.append((Modification.from_fields({r.name: r.to_domain() for r in rows}), amendment, rows))
        except (PermissionError, ValueError) as exc:
            raise Conflict(f"amendment {amendment.filename}: {exc}") from exc
    chain.sort(key=lambda link: link[0].effective)
    expansions = [link for link in chain if link[0].expansion == SEPARATE_LEASE]
    chain = [link for link in chain if link[0].expansion != SEPARATE_LEASE]

    try:
        schedule = build_schedule(terms, [m for m, _, _ in chain])
        lease_end = schedule.periods[-1].end_date
        separate = [
            SeparateLease(amendment, build_schedule(expansion_lease(m, terms.timing, lease_end)), rows)
            for m, amendment, rows in expansions
        ]
    except ValueError as exc:
        raise Conflict(str(exc)) from exc

    def usable(rows: list[FieldRow], names: tuple[str, ...]) -> dict[str, str]:
        return {r.name: r.id for r in rows if r.name in names and r.status != "rejected"}

    segments = [Segment(lease.id, 1, usable(lease_rows, LeaseTerms.FIELDS))]
    for (modification, amendment, rows), remeasurement in zip(chain, schedule.remeasurements):
        inputs = dict(segments[-1].inputs)
        if modification.rent is not None:  # a new rent replaces the old schedule's steps and free months
            inputs.pop("rent_steps", None)
            inputs.pop("free_periods", None)
        inputs.update(usable(rows, Modification.FIELDS))
        segments.append(Segment(amendment.id, remeasurement.after_period + 1, inputs))

    lineage = [lease.sha256] + [f"{a.sha256}@{m.effective.isoformat()}" for m, a, _ in chain] + [
        f"{a.sha256}@{m.effective.isoformat()}+separate" for m, a, _ in expansions]
    return ChainedSchedule(
        schedule=schedule,
        lease=lease,
        amendments=[a for _, a, _ in chain],
        fields=lease_rows + [r for _, _, rows in chain for r in rows],
        segments=segments,
        fingerprint=hashlib.sha256("|".join(lineage).encode()).hexdigest(),
        separate=separate,
    )
