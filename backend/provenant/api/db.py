"""Persistence. Every row carries tenant_id; Postgres adds RLS on top (db/postgres/001_hardening.sql)."""
from datetime import UTC, date, datetime
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from provenant.provenance import DocumentRef, ExtractedField, ReviewEvent, SourceRef, Status


def _now() -> datetime:
    return datetime.now(UTC)


def _uuid() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("tenant_id", "sha256"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    sha256: Mapped[str] = mapped_column(String(64))
    filename: Mapped[str] = mapped_column(String(255))
    size_bytes: Mapped[int] = mapped_column(Integer)
    uploaded_by: Mapped[str] = mapped_column(String(200))
    # lease | amendment | credit_agreement
    kind: Mapped[str] = mapped_column(String(16), default="lease", server_default="lease")
    # Hash lineage: an amendment names its lease by id and by the lease file's SHA-256, so the chain a
    # schedule was built from can be proven to be exactly these files.
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id"), index=True)
    parent_sha256: Mapped[str | None] = mapped_column(String(64))
    client_id: Mapped[str | None] = mapped_column(String(36), index=True)  # the client a lease belongs to
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|extracting|extracted|failed
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Job(Base):
    """Transactional outbox: written in the same transaction as the document, drained by the worker.

    No RLS on this table: the worker claims jobs across tenants, then does the work inside a tenant session.
    It holds ids only, never document content.
    """

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(32))
    document_id: Mapped[str] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending|running|done|dead
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ClientRow(Base):
    """A client of the firm (the tenant). Trial balances, mappings and tax computations belong to a client."""

    __tablename__ = "clients"
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ClientTrialBalanceRow(Base):
    __tablename__ = "client_trial_balances"
    __table_args__ = (UniqueConstraint("client_id", "year_end"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    year_end: Mapped[date] = mapped_column(Date)
    filename: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64))
    uploaded_by: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ClientTBLineRow(Base):
    __tablename__ = "client_tb_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    trial_balance_id: Mapped[str] = mapped_column(ForeignKey("client_trial_balances.id"), index=True)
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(255))
    balance_cents: Mapped[int] = mapped_column(BigInteger)


class AccountMappingRow(Base):
    """Current mapping of one client account (by code). Keyed by code, so it carries forward across years."""

    __tablename__ = "account_mappings"
    __table_args__ = (UniqueConstraint("client_id", "code"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    code: Mapped[str] = mapped_column(String(64))
    fs_line: Mapped[str | None] = mapped_column(String(64))
    tax_treatment: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))  # unmapped | suggested | confirmed
    rule: Mapped[str | None] = mapped_column(String(255))  # why it was suggested
    confirmed_by: Mapped[str | None] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class AccountMappingEventRow(Base):
    """Append-only history of mapping decisions: who mapped which account to what, and when."""

    __tablename__ = "account_mapping_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    code: Mapped[str] = mapped_column(String(64))
    fs_line: Mapped[str] = mapped_column(String(64))
    tax_treatment: Mapped[str] = mapped_column(String(32))
    by: Mapped[str] = mapped_column(String(200))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class TaxInputsRow(Base):
    """Preparer inputs for a client's year: losses brought forward and capital deduction classes (JSON)."""

    __tablename__ = "tax_inputs"
    __table_args__ = (UniqueConstraint("client_id", "year_end"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    year_end: Mapped[date] = mapped_column(Date)
    losses_brought_forward_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    allowances_json: Mapped[str] = mapped_column(Text, default="[]")
    credits_json: Mapped[str] = mapped_column(Text, default="[]")  # instalments, withholding, overpayments
    prior_return_json: Mapped[str] = mapped_column(Text, default="{}")  # reference and filing date of last year's return
    updated_by: Mapped[str] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class TaxApprovalRow(Base):
    """Append-only snapshot of a reviewed tax computation."""

    __tablename__ = "tax_approvals"
    __table_args__ = (UniqueConstraint("tenant_id", "client_id", "year_end", "fingerprint"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    year_end: Mapped[date] = mapped_column(Date)
    fingerprint: Mapped[str] = mapped_column(String(64))
    snapshot_json: Mapped[str] = mapped_column(Text)
    reviewer: Mapped[str] = mapped_column(String(200))
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class FixedAssetRegisterRow(Base):
    """A client's fixed asset register as at a year end. Uploading again replaces it."""

    __tablename__ = "fixed_asset_registers"
    __table_args__ = (UniqueConstraint("client_id", "year_end"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    year_end: Mapped[date] = mapped_column(Date)
    filename: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64))
    uploaded_by: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class FixedAssetRow(Base):
    __tablename__ = "fixed_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    register_id: Mapped[str] = mapped_column(ForeignKey("fixed_asset_registers.id", ondelete="CASCADE"), index=True)
    asset_code: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(String(255))
    asset_class: Mapped[str] = mapped_column(String(100))
    acquired_on: Mapped[date] = mapped_column(Date)
    cost_cents: Mapped[int] = mapped_column(BigInteger)
    disposed_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    proceeds_cents: Mapped[int] = mapped_column(BigInteger, default=0)


class ClientAttachmentRow(Base):
    """A supporting record for a client's year: bank statement, KRA certificate, receipt, invoice, payroll."""

    __tablename__ = "client_attachments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    year_end: Mapped[date] = mapped_column(Date)
    category: Mapped[str] = mapped_column(String(32))
    filename: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64))
    content_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(Integer)
    account_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    note: Mapped[str] = mapped_column(String(500), default="")
    uploaded_by: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PurchaseLedgerRow(Base):
    """A client's purchase ledger for a year, for eTIMS reconciliation. Uploading again replaces it.
    `applied_by` is set when the preparer applies the reconciliation's add-back to the tax computation."""

    __tablename__ = "purchase_ledgers"
    __table_args__ = (UniqueConstraint("client_id", "year_end"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    year_end: Mapped[date] = mapped_column(Date)
    filename: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64))
    uploaded_by: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    applied_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PurchaseLineRow(Base):
    __tablename__ = "purchase_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    ledger_id: Mapped[str] = mapped_column(ForeignKey("purchase_ledgers.id", ondelete="CASCADE"), index=True)
    row_number: Mapped[int] = mapped_column(Integer)
    date: Mapped[date] = mapped_column(Date)
    supplier: Mapped[str] = mapped_column(String(200))
    supplier_pin: Mapped[str] = mapped_column(String(16))
    invoice_number: Mapped[str] = mapped_column(String(64))
    account_code: Mapped[str] = mapped_column(String(64))
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    etims_number: Mapped[str] = mapped_column(String(64), default="")
    description: Mapped[str] = mapped_column(String(255), default="")


class EtimsExportRow(Base):
    """A client's eTIMS purchase invoices for a year, as exported from KRA. Uploading again replaces it."""

    __tablename__ = "etims_exports"
    __table_args__ = (UniqueConstraint("client_id", "year_end"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    year_end: Mapped[date] = mapped_column(Date)
    filename: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64))
    uploaded_by: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class EtimsInvoiceRow(Base):
    __tablename__ = "etims_invoices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    export_id: Mapped[str] = mapped_column(ForeignKey("etims_exports.id", ondelete="CASCADE"), index=True)
    row_number: Mapped[int] = mapped_column(Integer)
    cu_invoice_number: Mapped[str] = mapped_column(String(64))
    supplier_pin: Mapped[str] = mapped_column(String(16))
    supplier_name: Mapped[str] = mapped_column(String(200), default="")
    invoice_number: Mapped[str] = mapped_column(String(64))
    date: Mapped[date] = mapped_column(Date)
    taxable_cents: Mapped[int] = mapped_column(BigInteger)
    vat_cents: Mapped[int] = mapped_column(BigInteger, default=0)


class EtimsDecisionRow(Base):
    """A person's decision on one purchase (keyed so it survives re-uploads): exempt with a reason, or a match."""

    __tablename__ = "etims_decisions"
    __table_args__ = (UniqueConstraint("client_id", "year_end", "line_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    year_end: Mapped[date] = mapped_column(Date)
    line_key: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(16))  # exempt | match
    reason: Mapped[str] = mapped_column(String(500), default="")
    cu_invoice_number: Mapped[str] = mapped_column(String(64), default="")
    decided_by: Mapped[str] = mapped_column(String(200))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class TrialBalanceRow(Base):
    """A borrower's quarterly trial balance, attached to the credit agreement it is tested against."""

    __tablename__ = "trial_balances"
    __table_args__ = (UniqueConstraint("document_id", "period_end"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    period_end: Mapped[date] = mapped_column(Date)
    filename: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64))  # of the uploaded file, as evidence
    uploaded_by: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class TrialBalanceLineRow(Base):
    __tablename__ = "trial_balance_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    trial_balance_id: Mapped[str] = mapped_column(ForeignKey("trial_balances.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(32))
    balance_cents: Mapped[int] = mapped_column(BigInteger)  # integer cents: no float ever touches a balance


class FieldRow(Base):
    __tablename__ = "extracted_fields"
    __table_args__ = (UniqueConstraint("document_id", "name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    document_sha256: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(64))
    value: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float)
    page: Mapped[int] = mapped_column(Integer)
    x0: Mapped[float] = mapped_column(Float)
    top: Mapped[float] = mapped_column(Float)
    x1: Mapped[float] = mapped_column(Float)
    bottom: Mapped[float] = mapped_column(Float)
    char_start: Mapped[int] = mapped_column(Integer)
    char_end: Mapped[int] = mapped_column(Integer)
    extractor: Mapped[str] = mapped_column(String(64))
    entered_by: Mapped[str | None] = mapped_column(String(200))  # set for manual entries (maker-checker)
    status: Mapped[str] = mapped_column(String(16), index=True)
    gate_version: Mapped[str | None] = mapped_column(String(32))
    # Preparer inputs (page 0, no box): the stated basis, e.g. how the incremental borrowing rate was set.
    basis: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    @classmethod
    def from_domain(cls, tenant_id: str, document_id: str, f: ExtractedField) -> "FieldRow":
        located = f.source if isinstance(f.source, SourceRef) else None
        x0, top, x1, bottom = located.bbox if located else (0.0, 0.0, 0.0, 0.0)
        return cls(
            tenant_id=tenant_id,
            document_id=document_id,
            document_sha256=f.source.document_sha256,
            name=f.name,
            value=f.value,
            confidence=f.confidence,
            page=located.page if located else 0,
            x0=x0,
            top=top,
            x1=x1,
            bottom=bottom,
            char_start=located.char_start if located else 0,
            char_end=located.char_end if located else 0,
            extractor=f.extractor,
            status=f.status.value,
            gate_version=f.gate_version,
            basis=f.basis,
        )

    def to_domain(self) -> ExtractedField:
        source = DocumentRef(self.document_sha256) if self.page == 0 else SourceRef(
            self.document_sha256,
            self.page,
            (self.x0, self.top, self.x1, self.bottom),
            self.char_start,
            self.char_end,
        )
        return ExtractedField(
            name=self.name,
            value=self.value,
            confidence=self.confidence,
            source=source,
            extractor=self.extractor,
            status=Status(self.status),
            gate_version=self.gate_version,
            basis=self.basis,
        )


class ReviewEventRow(Base):
    """Append-only. The unique (tenant_id, prev_hash) makes concurrent reviews fail instead of forking the chain."""

    __tablename__ = "review_events"
    __table_args__ = (UniqueConstraint("tenant_id", "prev_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    field_id: Mapped[str] = mapped_column(ForeignKey("extracted_fields.id"))
    field_name: Mapped[str] = mapped_column(String(64))
    document_sha256: Mapped[str] = mapped_column(String(64))
    reviewer: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(16))
    before: Mapped[str] = mapped_column(Text)
    after: Mapped[str] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), unique=True)

    def to_domain(self) -> ReviewEvent:
        return ReviewEvent(
            field_name=self.field_name,
            document_sha256=self.document_sha256,
            reviewer=self.reviewer,
            action=Status(self.action),
            before=self.before,
            after=self.after,
            at=self.at,
            prev_hash=self.prev_hash,
            hash=self.hash,
        )


def make_engine(url: str) -> Engine:
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args)


def make_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    factory = sessionmaker(engine, expire_on_commit=False)
    if engine.dialect.name == "postgresql":

        @event.listens_for(factory, "after_begin")
        def _set_tenant(session: Session, transaction, connection) -> None:
            # RLS policies read this; it is transaction-local so pooled connections never leak it.
            connection.execute(
                text("select set_config('app.tenant_id', :t, true)"),
                {"t": session.info.get("tenant_id", "")},
            )

    return factory


def tenant_session(factory: sessionmaker[Session], tenant_id: str) -> Session:
    session = factory()
    session.info["tenant_id"] = tenant_id
    return session
