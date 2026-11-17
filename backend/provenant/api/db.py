"""Persistence. Every row carries tenant_id; Postgres adds RLS on top (db/postgres/001_hardening.sql)."""
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
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

from provenant.provenance import ExtractedField, ReviewEvent, SourceRef, Status


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
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    @classmethod
    def from_domain(cls, tenant_id: str, document_id: str, f: ExtractedField) -> "FieldRow":
        x0, top, x1, bottom = f.source.bbox
        return cls(
            tenant_id=tenant_id,
            document_id=document_id,
            document_sha256=f.source.document_sha256,
            name=f.name,
            value=f.value,
            confidence=f.confidence,
            page=f.source.page,
            x0=x0,
            top=top,
            x1=x1,
            bottom=bottom,
            char_start=f.source.char_start,
            char_end=f.source.char_end,
            extractor=f.extractor,
            status=f.status.value,
            gate_version=f.gate_version,
        )

    def to_domain(self) -> ExtractedField:
        return ExtractedField(
            name=self.name,
            value=self.value,
            confidence=self.confidence,
            source=SourceRef(
                self.document_sha256,
                self.page,
                (self.x0, self.top, self.x1, self.bottom),
                self.char_start,
                self.char_end,
            ),
            extractor=self.extractor,
            status=Status(self.status),
            gate_version=self.gate_version,
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
