"""Persistence. Every row carries tenant_id; Postgres adds RLS on top (db/postgres/001_hardening.sql)."""
import os
from datetime import UTC, date, datetime
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    false,
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


class ComplianceReturnRow(Base):
    """A return the client filed, as a CSV (VAT returns, PAYE returns, WHT certificates), per client-year.
    The validated text is kept, so a check can always be re-run from what was uploaded."""

    __tablename__ = "compliance_returns"
    __table_args__ = (UniqueConstraint("client_id", "year_end", "kind"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    year_end: Mapped[date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(32))  # vat | paye | wht
    filename: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text)
    uploaded_by: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class VarianceExplanationRow(Base):
    __tablename__ = "variance_explanations"
    __table_args__ = (UniqueConstraint("client_id", "year_end", "check_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    year_end: Mapped[date] = mapped_column(Date)
    check_key: Mapped[str] = mapped_column(String(32))
    text: Mapped[str] = mapped_column(Text)
    explained_by: Mapped[str] = mapped_column(String(200))
    explained_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class StatementRow(Base):
    """An M-Pesa or bank statement for a client-year, as uploaded (the validated CSV text is kept)."""

    __tablename__ = "statements"
    __table_args__ = (UniqueConstraint("client_id", "year_end", "sha256"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    year_end: Mapped[date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(16))  # mpesa | bank
    label: Mapped[str] = mapped_column(String(120))  # e.g. "M-Pesa Till 123456", "Equity Bank 0123"
    filename: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text)
    uploaded_by: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class StatementCategoryRow(Base):
    """A person's confirmed category for one statement movement, keyed so it survives re-uploads."""

    __tablename__ = "statement_categories"
    __table_args__ = (UniqueConstraint("client_id", "year_end", "txn_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    year_end: Mapped[date] = mapped_column(Date)
    txn_key: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(32))
    decided_by: Mapped[str] = mapped_column(String(200))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class MemoDraftRow(Base):
    """The board tax memo's text for a client-year: prose with fields bound to the computation (ADR 0008)."""

    __tablename__ = "memo_drafts"
    __table_args__ = (UniqueConstraint("client_id", "year_end"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    year_end: Mapped[date] = mapped_column(Date)
    body: Mapped[str] = mapped_column(Text)
    updated_by: Mapped[str] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class GeneralLedgerRow(Base):
    """A client's general ledger for a year: the uploaded CSV and its normalised Parquet, both content-addressed
    in the blob store. Uploading again replaces it (ADR 0009)."""

    __tablename__ = "general_ledgers"
    __table_args__ = (UniqueConstraint("client_id", "year_end"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    year_end: Mapped[date] = mapped_column(Date)
    filename: Mapped[str] = mapped_column(String(255))
    csv_sha256: Mapped[str] = mapped_column(String(64))
    parquet_sha256: Mapped[str] = mapped_column(String(64))
    lines: Mapped[int] = mapped_column(BigInteger)
    approval_limit_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    uploaded_by: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class JournalExceptionRow(Base):
    """A person's decision on a flagged entry for one test: cleared (reviewer, with a reason) or escalated."""

    __tablename__ = "journal_exceptions"
    __table_args__ = (UniqueConstraint("client_id", "year_end", "entry_id", "test"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    year_end: Mapped[date] = mapped_column(Date)
    entry_id: Mapped[str] = mapped_column(String(64))
    test: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))  # cleared | escalated
    reason: Mapped[str] = mapped_column(String(1000))
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


class CovenantPeriodInputRow(Base):
    """Facts of one test date that no trial balance holds. Used only once someone other than the enterer confirms."""

    __tablename__ = "covenant_period_inputs"
    __table_args__ = (UniqueConstraint("document_id", "period_end"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    period_end: Mapped[date] = mapped_column(Date)
    revolver_drawn_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    delivered_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    basis: Mapped[str] = mapped_column(String(1000))
    entered_by: Mapped[str] = mapped_column(String(200))
    entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    confirmed_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CovenantAdjustmentRow(Base):
    """An EBITDA add-back or deduction, or a lease under frozen GAAP. Used only once a partner approves it."""

    __tablename__ = "covenant_adjustments"
    __table_args__ = (UniqueConstraint("document_id", "lease_document_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # addback | frozen_lease
    quarter_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    label: Mapped[str] = mapped_column(String(255))
    amount_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # negative for a deduction
    capped: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    lease_document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id"), nullable=True)
    clause: Mapped[str] = mapped_column(String(255))
    basis: Mapped[str] = mapped_column(String(1000))
    entered_by: Mapped[str] = mapped_column(String(200))
    entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    approved_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LegalPlaybookRow(Base):
    """A firm's own positions for contract review; topics it has not changed keep the standard position."""

    __tablename__ = "legal_playbooks"

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    positions: Mapped[str] = mapped_column(Text)  # JSON: topic key -> position
    updated_by: Mapped[str] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ContractReviewRow(Base):
    """One run of the review agent, with the exact playbook it used."""

    __tablename__ = "contract_reviews"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued | running | done | failed
    agent: Mapped[str | None] = mapped_column(String(64), nullable=True)
    playbook_version: Mapped[str] = mapped_column(String(32))
    playbook_sha256: Mapped[str] = mapped_column(String(64))
    playbook: Mapped[str] = mapped_column(Text)  # JSON: topic key -> position, as run
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    not_found: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")  # JSON list of topic keys
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    requested_by: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ContractFindingRow(Base):
    """An issue with a quote located on a PDF page. It means nothing until a lawyer accepts it."""

    __tablename__ = "contract_findings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    review_id: Mapped[str] = mapped_column(ForeignKey("contract_reviews.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"))
    filename: Mapped[str] = mapped_column(String(255))
    topic: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(8))
    title: Mapped[str] = mapped_column(String(200))
    explanation: Mapped[str] = mapped_column(Text)
    suggestion: Mapped[str] = mapped_column(Text)
    quote: Mapped[str] = mapped_column(Text)
    document_sha256: Mapped[str] = mapped_column(String(64))
    page: Mapped[int] = mapped_column(Integer)
    x0: Mapped[float] = mapped_column(Float)
    top: Mapped[float] = mapped_column(Float)
    x1: Mapped[float] = mapped_column(Float)
    bottom: Mapped[float] = mapped_column(Float)
    char_start: Mapped[int] = mapped_column(Integer)
    char_end: Mapped[int] = mapped_column(Integer)
    proposed_term: Mapped[str | None] = mapped_column(String(64), nullable=True)
    proposed_value: Mapped[str | None] = mapped_column(String(200), nullable=True)
    decision: Mapped[str | None] = mapped_column(String(8), nullable=True)  # accepted | rejected
    decision_note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    field_id: Mapped[str | None] = mapped_column(String(36), nullable=True)  # the term an acceptance put up for review


class ContractQuestionRow(Base):
    __tablename__ = "contract_questions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    review_id: Mapped[str] = mapped_column(ForeignKey("contract_reviews.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    question: Mapped[str] = mapped_column(String(1000))
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued | running | done | failed
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    not_stated: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    citations: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")  # JSON, each located on a page
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    asked_by: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ContractDraftRow(Base):
    """A paralegal draft. Its wording at every step is kept in contract_draft_events (ADR 0015)."""

    __tablename__ = "contract_drafts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # notice | amendment | letter
    instructions: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|running|failed|ready|returned|approved
    agent: Mapped[str | None] = mapped_column(String(64), nullable=True)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    body: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")  # JSON paragraphs with citations
    open_points: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")
    version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    requested_by: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class ContractDraftEventRow(Base):
    """Append-only: who generated, edited, returned or approved which wording."""

    __tablename__ = "contract_draft_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    draft_id: Mapped[str] = mapped_column(ForeignKey("contract_drafts.id"), index=True)
    action: Mapped[str] = mapped_column(String(16))
    version: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    content_sha256: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AiRunRow(Base):
    """Append-only usage of one Claude run. Holds no prompt, document text, tool input or model output (ADR 0014)."""

    __tablename__ = "ai_runs"
    __table_args__ = (Index("ix_ai_runs_tenant_created", "tenant_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64))
    document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    purpose: Mapped[str] = mapped_column(String(32))
    models: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16))  # completed | failed | refused | deadline | budget
    stop_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    turns: Mapped[int] = mapped_column(Integer)
    tool_calls: Mapped[int] = mapped_column(Integer)
    input_tokens: Mapped[int] = mapped_column(BigInteger)
    output_tokens: Mapped[int] = mapped_column(BigInteger)
    cache_read_tokens: Mapped[int] = mapped_column(BigInteger)
    cache_write_tokens: Mapped[int] = mapped_column(BigInteger)
    cost_microusd: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # an estimate; None if unpriced
    prices_version: Mapped[str] = mapped_column(String(16))
    duration_ms: Mapped[int] = mapped_column(BigInteger)
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
    if url.startswith("sqlite"):
        return create_engine(url, connect_args={"check_same_thread": False})
    # A hosted database or pooler can drop idle connections and caps how many each client may hold.
    return create_engine(url, pool_pre_ping=True, pool_recycle=300,
                         pool_size=int(os.environ.get("PROVENANT_DB_POOL_SIZE", "5")),
                         max_overflow=int(os.environ.get("PROVENANT_DB_MAX_OVERFLOW", "10")))


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
