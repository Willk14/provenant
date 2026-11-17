"""HTTP API. Run: uvicorn provenant.api.main:app   (after: python -m provenant.api.migrate)"""
import logging
import os
from collections.abc import Iterator
from contextlib import asynccontextmanager
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from threading import Event, Thread
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, File, Header, HTTPException, Response, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from provenant.api import services
from provenant.api.auth import AuthError, Authenticator, Principal, authenticator_from_env, principal_from
from provenant.api.db import Document, FieldRow, make_engine, make_sessionmaker, tenant_session
from provenant.api.export import schedule_workbook
from provenant.api.storage import BlobStore
from provenant.extraction import Extractor, extractor_from_env
from provenant.provenance import Status
from provenant.worker import run_forever

log = logging.getLogger(__name__)


# ---- response models -------------------------------------------------------------------------


class FieldOut(BaseModel):
    id: str
    name: str
    value: str
    confidence: float
    status: str
    gate_version: str | None
    extractor: str
    entered_by: str | None
    page: int
    bbox: tuple[float, float, float, float]
    char_start: int
    char_end: int

    @classmethod
    def of(cls, r: FieldRow) -> "FieldOut":
        return cls(
            id=r.id, name=r.name, value=r.value, confidence=r.confidence, status=r.status,
            gate_version=r.gate_version, extractor=r.extractor, entered_by=r.entered_by, page=r.page,
            bbox=(r.x0, r.top, r.x1, r.bottom), char_start=r.char_start, char_end=r.char_end,
        )


class DocumentOut(BaseModel):
    id: str
    sha256: str
    filename: str
    size_bytes: int
    uploaded_by: str
    status: str
    error: str | None
    created_at: datetime
    fields: list[FieldOut] = []

    @classmethod
    def of(cls, d: Document, fields: list[FieldRow] = ()) -> "DocumentOut":
        return cls(
            id=d.id, sha256=d.sha256, filename=d.filename, size_bytes=d.size_bytes, uploaded_by=d.uploaded_by,
            status=d.status, error=d.error, created_at=d.created_at, fields=[FieldOut.of(f) for f in fields],
        )


class ReviewIn(BaseModel):
    action: Literal["approved", "corrected", "rejected"]
    corrected_value: str | None = Field(default=None, max_length=200)


class FieldIn(BaseModel):
    name: str = Field(max_length=64)
    value: str = Field(max_length=200)
    page: int = Field(ge=1)
    bbox: tuple[float, float, float, float]


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
    authenticator: Authenticator | None = None,
    dev_auth: bool | None = None,
    inline_worker: bool | None = None,
) -> FastAPI:
    """Schema is managed by migrations (python -m provenant.api.migrate); this never creates tables."""
    database_url = database_url or os.environ.get("DATABASE_URL", "sqlite:///./provenant.db")
    store = BlobStore(storage_dir or Path(os.environ.get("PROVENANT_STORAGE_DIR", "./storage")))
    extractor = extractor or extractor_from_env()
    authenticator = authenticator or authenticator_from_env()
    dev_auth = os.environ.get("PROVENANT_DEV_AUTH") == "1" if dev_auth is None else dev_auth
    inline_worker = os.environ.get("PROVENANT_INLINE_WORKER") == "1" if inline_worker is None else inline_worker
    if authenticator is None and not dev_auth:
        raise RuntimeError(
            "No authentication configured: set PROVENANT_JWT_ISSUER/AUDIENCE/JWKS_URL, "
            "or PROVENANT_DEV_AUTH=1 for local development"
        )
    if authenticator is not None and dev_auth:
        raise RuntimeError("PROVENANT_DEV_AUTH cannot be combined with JWT authentication")
    if dev_auth:
        log.warning("PROVENANT_DEV_AUTH is on: identity comes from X-Tenant-Id / X-User / X-Roles headers.")

    factory = make_sessionmaker(make_engine(database_url))

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        stop = Event()
        if inline_worker:  # dev convenience; production runs `python -m provenant.worker` separately
            Thread(target=run_forever, args=(factory, store, extractor, stop), daemon=True).start()
        yield
        stop.set()

    app = FastAPI(title="Provenant", version="0.3.0", summary="Every number knows where it came from.", lifespan=lifespan)

    def principal(
        authorization: Annotated[str | None, Header()] = None,
        x_tenant_id: Annotated[str | None, Header()] = None,
        x_user: Annotated[str | None, Header()] = None,
        x_roles: Annotated[str | None, Header()] = None,
    ) -> Principal:
        try:
            if authenticator is not None:
                scheme, _, token = (authorization or "").partition(" ")
                if scheme.lower() != "bearer" or not token:
                    raise AuthError("bearer token required")
                p = authenticator(token)
            else:  # dev only: roles default to all three so one person can click through locally
                p = principal_from(x_tenant_id, x_user, x_roles or "preparer,reviewer,partner")
        except AuthError as exc:
            raise HTTPException(401, str(exc), headers={"WWW-Authenticate": "Bearer"}) from exc
        if not p.roles:
            raise HTTPException(403, "no Provenant role assigned")
        return p

    def session(p: Annotated[Principal, Depends(principal)]) -> Iterator[Session]:
        with tenant_session(factory, p.tenant_id) as s:
            yield s

    Auth = Annotated[Principal, Depends(principal)]
    Db = Annotated[Session, Depends(session)]

    for exc_type, code in (
        (services.NotFound, 404),
        (services.Conflict, 409),
        (services.Invalid, 422),
        (services.Forbidden, 403),
    ):
        app.add_exception_handler(
            exc_type, lambda _r, e, code=code: JSONResponse({"detail": str(e)}, status_code=code)
        )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/documents", response_model=DocumentOut, status_code=201)
    def upload(p: Auth, s: Db, response: Response, file: Annotated[UploadFile, File()]) -> DocumentOut:
        data = file.file.read(services.MAX_UPLOAD_BYTES + 1)  # sync route: runs in the threadpool, like the DB calls
        doc, created = services.upload_document(s, store, p, file.filename or "", data)
        if not created:
            response.status_code = 200
        return DocumentOut.of(doc)

    @app.get("/documents/{document_id}", response_model=DocumentOut)
    def get_document(document_id: str, p: Auth, s: Db) -> DocumentOut:
        doc = services.get_document(s, p.tenant_id, document_id)
        return DocumentOut.of(doc, services.list_fields(s, p.tenant_id, document_id))

    @app.get("/documents/{document_id}/file")
    def get_file(document_id: str, p: Auth, s: Db) -> Response:
        doc = services.get_document(s, p.tenant_id, document_id)
        return Response(store.get(doc.sha256), media_type="application/pdf")

    @app.post("/documents/{document_id}/fields", response_model=FieldOut, status_code=201)
    def enter_field(document_id: str, body: FieldIn, p: Auth, s: Db) -> FieldOut:
        row = services.enter_field(s, store, p, document_id, body.name, body.value, body.page, body.bbox)
        return FieldOut.of(row)

    @app.get("/review-queue", response_model=list[FieldOut])
    def queue(p: Auth, s: Db) -> list[FieldOut]:
        return [FieldOut.of(r) for r in services.review_queue(s, p.tenant_id)]

    @app.post("/fields/{field_id}/review", response_model=FieldOut)
    def review(field_id: str, body: ReviewIn, p: Auth, s: Db) -> FieldOut:
        return FieldOut.of(services.review_field(s, p, field_id, Status(body.action), body.corrected_value))

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

    app.state.session_factory, app.state.store, app.state.extractor = factory, store, extractor
    return app


def __getattr__(name: str):  # lazy so importing this module (e.g. in tests) never touches the default database
    if name == "app":
        return create_app()
    raise AttributeError(name)
