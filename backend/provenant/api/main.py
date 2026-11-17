"""HTTP API. Run: uvicorn provenant.api.main:app --reload"""
import logging
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal

from fastapi import BackgroundTasks, Depends, FastAPI, File, Header, HTTPException, Response, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from provenant.api import services
from provenant.api.db import Base, Document, FieldRow, make_engine, make_sessionmaker, tenant_session
from provenant.api.export import schedule_workbook
from provenant.api.storage import BlobStore
from provenant.extraction import Extractor
from provenant.extraction.lease_labels import LeaseLabelExtractor
from provenant.provenance import Status

log = logging.getLogger(__name__)
_TENANT = re.compile(r"[A-Za-z0-9_-]{1,64}")


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    user: str


# ---- response models -------------------------------------------------------------------------


class FieldOut(BaseModel):
    id: str
    name: str
    value: str
    confidence: float
    status: str
    gate_version: str | None
    extractor: str
    page: int
    bbox: tuple[float, float, float, float]
    char_start: int
    char_end: int

    @classmethod
    def of(cls, r: FieldRow) -> "FieldOut":
        return cls(
            id=r.id, name=r.name, value=r.value, confidence=r.confidence, status=r.status,
            gate_version=r.gate_version, extractor=r.extractor, page=r.page,
            bbox=(r.x0, r.top, r.x1, r.bottom), char_start=r.char_start, char_end=r.char_end,
        )


class DocumentOut(BaseModel):
    id: str
    sha256: str
    filename: str
    size_bytes: int
    status: str
    error: str | None
    created_at: datetime
    fields: list[FieldOut] = []

    @classmethod
    def of(cls, d: Document, fields: list[FieldRow] = ()) -> "DocumentOut":
        return cls(
            id=d.id, sha256=d.sha256, filename=d.filename, size_bytes=d.size_bytes, status=d.status,
            error=d.error, created_at=d.created_at, fields=[FieldOut.of(f) for f in fields],
        )


class ReviewIn(BaseModel):
    action: Literal["approved", "corrected", "rejected"]
    corrected_value: str | None = None


class PeriodOut(BaseModel):
    number: int
    end_date: date
    opening: Decimal
    interest: Decimal
    payment: Decimal
    closing: Decimal
    depreciation: Decimal


class ScheduleOut(BaseModel):
    initial_liability: Decimal
    initial_rou_asset: Decimal
    periods: list[PeriodOut]
    inputs: list[FieldOut]


class ReviewLogStatus(BaseModel):
    intact: bool
    events: int


# ---- app -------------------------------------------------------------------------------------


def create_app(
    database_url: str | None = None,
    storage_dir: Path | None = None,
    extractor: Extractor | None = None,
    dev_auth: bool | None = None,
) -> FastAPI:
    database_url = database_url or os.environ.get("DATABASE_URL", "sqlite:///./provenant.db")
    store = BlobStore(storage_dir or Path(os.environ.get("PROVENANT_STORAGE_DIR", "./storage")))
    extractor = extractor or LeaseLabelExtractor()
    dev_auth = os.environ.get("PROVENANT_DEV_AUTH", "1") == "1" if dev_auth is None else dev_auth

    engine = make_engine(database_url)
    Base.metadata.create_all(engine)  # shortcut: replace with Alembic migrations before a shared environment
    factory = make_sessionmaker(engine)
    if dev_auth:
        log.warning("PROVENANT_DEV_AUTH is on: identity comes from X-Tenant-Id / X-User headers. Local use only.")

    app = FastAPI(title="Provenant", version="0.2.0", summary="Every number knows where it came from.")

    def principal(
        x_tenant_id: Annotated[str | None, Header()] = None,
        x_user: Annotated[str | None, Header()] = None,
    ) -> Principal:
        # shortcut: header identity for local development; replace with verified JWTs (OIDC) before deployment.
        if not dev_auth:
            raise HTTPException(501, "authentication is not configured")
        if not x_tenant_id or not _TENANT.fullmatch(x_tenant_id) or not x_user or len(x_user) > 200:
            raise HTTPException(401, "X-Tenant-Id and X-User headers are required")
        return Principal(x_tenant_id, x_user)

    def session(p: Annotated[Principal, Depends(principal)]) -> Iterator[Session]:
        with tenant_session(factory, p.tenant_id) as s:
            yield s

    Auth = Annotated[Principal, Depends(principal)]
    Db = Annotated[Session, Depends(session)]

    for exc_type, code in ((services.NotFound, 404), (services.Conflict, 409), (services.Invalid, 422)):
        app.add_exception_handler(
            exc_type, lambda _r, e, code=code: JSONResponse({"detail": str(e)}, status_code=code)
        )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/documents", response_model=DocumentOut, status_code=201)
    def upload(
        p: Auth, s: Db, response: Response, background: BackgroundTasks, file: Annotated[UploadFile, File()]
    ) -> DocumentOut:
        data = file.file.read(services.MAX_UPLOAD_BYTES + 1)  # sync route: runs in the threadpool, like the DB calls
        doc, created = services.upload_document(s, store, p.tenant_id, file.filename or "", data)
        if not created:
            response.status_code = 200
        if doc.status in ("uploaded", "failed"):
            background.add_task(services.run_extraction, factory, store, extractor, p.tenant_id, doc.id)
        return DocumentOut.of(doc)

    @app.get("/documents/{document_id}", response_model=DocumentOut)
    def get_document(document_id: str, p: Auth, s: Db) -> DocumentOut:
        doc = services.get_document(s, p.tenant_id, document_id)
        return DocumentOut.of(doc, services.list_fields(s, p.tenant_id, document_id))

    @app.get("/documents/{document_id}/file")
    def get_file(document_id: str, p: Auth, s: Db) -> Response:
        doc = services.get_document(s, p.tenant_id, document_id)
        return Response(store.get(doc.sha256), media_type="application/pdf")

    @app.get("/review-queue", response_model=list[FieldOut])
    def queue(p: Auth, s: Db) -> list[FieldOut]:
        return [FieldOut.of(r) for r in services.review_queue(s, p.tenant_id)]

    @app.post("/fields/{field_id}/review", response_model=FieldOut)
    def review(field_id: str, body: ReviewIn, p: Auth, s: Db) -> FieldOut:
        row = services.review_field(s, p.tenant_id, p.user, field_id, Status(body.action), body.corrected_value)
        return FieldOut.of(row)

    @app.get("/review-log/verify", response_model=ReviewLogStatus)
    def verify_log(p: Auth, s: Db) -> ReviewLogStatus:
        intact, events = services.review_log_is_intact(s, p.tenant_id)
        return ReviewLogStatus(intact=intact, events=events)

    @app.get("/documents/{document_id}/lease-schedule", response_model=ScheduleOut)
    def schedule(document_id: str, p: Auth, s: Db) -> ScheduleOut:
        sched, rows = services.lease_schedule(s, p.tenant_id, document_id)
        return ScheduleOut(
            initial_liability=sched.initial_liability,
            initial_rou_asset=sched.initial_rou_asset,
            periods=[PeriodOut(**vars(period)) for period in sched.periods],
            inputs=[FieldOut.of(r) for r in rows],
        )

    @app.get("/documents/{document_id}/lease-schedule.xlsx")
    def schedule_xlsx(document_id: str, p: Auth, s: Db) -> Response:
        sched, rows = services.lease_schedule(s, p.tenant_id, document_id)
        return Response(
            schedule_workbook(sched, rows),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="lease-schedule-{document_id}.xlsx"'},
        )

    return app


def __getattr__(name: str):  # lazy so importing this module (e.g. in tests) never touches the default database
    if name == "app":
        return create_app()
    raise AttributeError(name)
