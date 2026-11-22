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

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Response, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from provenant.api import covenants, etims, ledger, memo, services, statements, tax_routes, variance
from provenant.covenants.engine import quarters_back
from provenant.api.auth import AuthError, Authenticator, Principal, authenticator_from_env, principal_from
from provenant.api.db import Document, FieldRow, make_engine, make_sessionmaker, tenant_session
from provenant.api.export import schedule_workbook
from provenant.api.storage import BlobStore, make_store
from provenant.extraction import Extractor, extractor_from_env
from provenant.provenance import Status
from provenant.worker import run_forever

log = logging.getLogger(__name__)


# ---- response models -------------------------------------------------------------------------


class FieldOut(BaseModel):
    id: str
    document_id: str
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
    basis: str | None = None  # set for preparer inputs (page 0): values the document does not state

    @classmethod
    def of(cls, r: FieldRow) -> "FieldOut":
        return cls(
            id=r.id, document_id=r.document_id, name=r.name, value=r.value, confidence=r.confidence, status=r.status,
            gate_version=r.gate_version, extractor=r.extractor, entered_by=r.entered_by, page=r.page,
            bbox=(r.x0, r.top, r.x1, r.bottom), char_start=r.char_start, char_end=r.char_end, basis=r.basis,
        )


class DocumentOut(BaseModel):
    id: str
    sha256: str
    filename: str
    size_bytes: int
    uploaded_by: str
    kind: Literal["lease", "amendment", "credit_agreement"]
    parent_id: str | None
    parent_sha256: str | None
    client_id: str | None = None
    status: str
    error: str | None
    created_at: datetime
    needs_review: int | None = None
    missing_terms: int | None = None
    fields: list[FieldOut] = []
    amendments: list["DocumentOut"] = []  # for a lease, in upload order (the schedule orders by effective date)
    parent: "DocumentOut | None" = None  # for an amendment, its lease (without fields)

    @classmethod
    def of(
        cls,
        d: Document,
        fields: list[FieldRow] = (),
        needs_review: int | None = None,
        missing_terms: int | None = None,
        amendments: list[Document] = (),
        parent: Document | None = None,
    ) -> "DocumentOut":
        return cls(
            id=d.id, sha256=d.sha256, filename=d.filename, size_bytes=d.size_bytes, uploaded_by=d.uploaded_by,
            kind=d.kind, parent_id=d.parent_id, parent_sha256=d.parent_sha256, client_id=d.client_id,
            status=d.status, error=d.error, created_at=d.created_at, needs_review=needs_review,
            missing_terms=missing_terms, fields=[FieldOut.of(f) for f in fields],
            amendments=[DocumentOut.of(a) for a in amendments], parent=DocumentOut.of(parent) if parent else None,
        )


class MeOut(BaseModel):
    tenant_id: str
    user: str
    roles: list[str]


class ReviewIn(BaseModel):
    action: Literal["approved", "corrected", "rejected"]
    corrected_value: str | None = Field(default=None, max_length=200)


class InputIn(BaseModel):
    name: str = Field(max_length=64)
    value: str = Field(max_length=64)
    basis: str = Field(max_length=1000)


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
    segment: int


class RemeasurementOut(BaseModel):
    after_period: int
    effective: date
    document_id: str
    filename: str
    liability_before: Decimal
    liability_after: Decimal
    rou_before: Decimal
    rou_adjustment: Decimal
    gain: Decimal
    remeasured_on: date | None = None  # the period boundary it is posted at, on or after `effective`
    terms_start: date | None = None  # when the new rent or term starts


class SegmentOut(BaseModel):
    document_id: str
    from_period: int
    inputs: dict[str, str]  # term -> field id


class SeparateLeaseOut(BaseModel):
    """Added space accounted for as its own lease (IFRS 16.44), from an expansion amendment."""

    document_id: str
    filename: str
    commencement: date
    initial_liability: Decimal
    initial_rou_asset: Decimal
    periods: list[PeriodOut]
    inputs: list[FieldOut]


class ScheduleOut(BaseModel):
    initial_liability: Decimal
    initial_rou_asset: Decimal
    periods: list[PeriodOut]
    remeasurements: list[RemeasurementOut]
    segments: list[SegmentOut]
    chain_fingerprint: str
    inputs: list[FieldOut]
    separate_leases: list[SeparateLeaseOut] = []


class ReviewLogStatus(BaseModel):
    intact: bool
    events: int


class TrialBalanceOut(BaseModel):
    id: str
    period_end: date
    filename: str
    sha256: str
    uploaded_by: str
    lines: int


class WorkingOut(BaseModel):
    label: str
    amount: Decimal


class CovenantResultOut(BaseModel):
    kind: str
    field_id: str
    threshold: Decimal
    status: str
    value: Decimal | None  # ratio to 4 dp, or the amount; display only (tests compare the exact value)
    headroom: Decimal | None  # fraction, 4 dp
    workings: list[WorkingOut]
    note: str


class TestDateOut(BaseModel):
    period_end: date
    certificate_due: date
    cure_deadline: date | None
    cure_pending: bool
    status: Literal["tested", "awaiting", "overdue"]
    missing: str | None
    trial_balance_ids: list[str]
    results: list[CovenantResultOut]


class AlertOut(BaseModel):
    severity: str
    on: date
    message: str


class CovenantTermsOut(BaseModel):
    first_test_date: date
    reporting_days: int
    cure_days: int | None
    warning_headroom: Decimal
    cure_day_basis: Literal["calendar", "business"]
    cure_runs_from: Literal["due date", "delivery"]
    springing_usage: Decimal | None
    revolver_commitment: Decimal | None


class PeriodInputIn(BaseModel):
    revolver_drawn: str | None = Field(default=None, max_length=32)
    delivered_on: date | None = None
    basis: str = Field(max_length=1000)


class PeriodInputOut(BaseModel):
    period_end: date
    revolver_drawn: Decimal | None
    delivered_on: date | None
    basis: str
    entered_by: str
    confirmed_by: str | None

    @classmethod
    def of(cls, r) -> "PeriodInputOut":
        drawn = None if r.revolver_drawn_cents is None else Decimal(r.revolver_drawn_cents) * covenants.CENT
        return cls(period_end=r.period_end, revolver_drawn=drawn, delivered_on=r.delivered_on, basis=r.basis,
                   entered_by=r.entered_by, confirmed_by=r.confirmed_by)


class CovenantsOut(BaseModel):
    as_of: date
    terms: CovenantTermsOut
    test_dates: list[TestDateOut]
    alerts: list[AlertOut]
    inputs: list[FieldOut]
    period_inputs: list[PeriodInputOut]


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
    store = make_store(storage_dir)  # local disk, or an S3-compatible bucket when PROVENANT_STORAGE=s3
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
    def upload(
        p: Auth,
        s: Db,
        response: Response,
        file: Annotated[UploadFile, File()],
        amends: Annotated[str | None, Form(max_length=36)] = None,
        kind: Annotated[str, Form(max_length=32)] = "lease",
    ) -> DocumentOut:
        data = file.file.read(services.MAX_UPLOAD_BYTES + 1)  # sync route: runs in the threadpool, like the DB calls
        doc, created = services.upload_document(
            s, store, p, file.filename or "", data, amends=amends or None, kind=kind
        )
        if not created:
            response.status_code = 200
        return DocumentOut.of(doc)

    @app.get("/me", response_model=MeOut)
    def me(p: Auth) -> MeOut:
        return MeOut(tenant_id=p.tenant_id, user=p.user, roles=sorted(p.roles))

    @app.get("/documents", response_model=list[DocumentOut])
    def documents(p: Auth, s: Db) -> list[DocumentOut]:
        return [
            DocumentOut.of(d, needs_review=n, missing_terms=m) for d, n, m in services.list_documents(s, p.tenant_id)
        ]

    @app.get("/documents/{document_id}", response_model=DocumentOut)
    def get_document(document_id: str, p: Auth, s: Db) -> DocumentOut:
        doc = services.get_document(s, p.tenant_id, document_id)
        return DocumentOut.of(
            doc,
            services.list_fields(s, p.tenant_id, document_id),
            amendments=services.list_amendments(s, p.tenant_id, doc.id) if doc.kind == "lease" else (),
            parent=services.get_document(s, p.tenant_id, doc.parent_id) if doc.parent_id else None,
        )

    @app.get("/documents/{document_id}/file")
    def get_file(document_id: str, p: Auth, s: Db) -> Response:
        doc = services.get_document(s, p.tenant_id, document_id)
        return Response(store.get(doc.sha256), media_type="application/pdf")

    @app.post("/documents/{document_id}/fields", response_model=FieldOut, status_code=201)
    def enter_field(document_id: str, body: FieldIn, p: Auth, s: Db) -> FieldOut:
        row = services.enter_field(s, store, p, document_id, body.name, body.value, body.page, body.bbox)
        return FieldOut.of(row)

    @app.post("/documents/{document_id}/inputs", response_model=FieldOut, status_code=201)
    def enter_input(document_id: str, body: InputIn, p: Auth, s: Db) -> FieldOut:
        return FieldOut.of(services.enter_input(s, p, document_id, body.name, body.value, body.basis))

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
        chained = services.lease_schedule(s, p.tenant_id, document_id)
        sched = chained.schedule
        return ScheduleOut(
            initial_liability=sched.initial_liability,
            initial_rou_asset=sched.initial_rou_asset,
            periods=[PeriodOut(**vars(period)) for period in sched.periods],
            remeasurements=[
                RemeasurementOut(document_id=a.id, filename=a.filename, **{k: v for k, v in vars(r).items()})
                for r, a in zip(sched.remeasurements, chained.amendments)
            ],
            segments=[SegmentOut(**vars(seg)) for seg in chained.segments],
            chain_fingerprint=chained.fingerprint,
            inputs=[FieldOut.of(r) for r in chained.fields],
            separate_leases=[
                SeparateLeaseOut(
                    document_id=x.amendment.id, filename=x.amendment.filename,
                    commencement=x.schedule.terms.commencement, initial_liability=x.schedule.initial_liability,
                    initial_rou_asset=x.schedule.initial_rou_asset,
                    periods=[PeriodOut(**vars(period)) for period in x.schedule.periods],
                    inputs=[FieldOut.of(r) for r in x.fields],
                )
                for x in chained.separate
            ],
        )

    @app.get("/documents/{document_id}/lease-schedule.xlsx")
    def schedule_xlsx(document_id: str, p: Auth, s: Db) -> Response:
        chained = services.lease_schedule(s, p.tenant_id, document_id)
        return Response(
            schedule_workbook(chained),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="lease-schedule-{document_id}.xlsx"'},
        )

    @app.post("/documents/{document_id}/trial-balances", response_model=TrialBalanceOut, status_code=201)
    def upload_trial_balance(
        document_id: str,
        p: Auth,
        s: Db,
        file: Annotated[UploadFile, File()],
        period_end: Annotated[date, Form()],
    ) -> TrialBalanceOut:
        data = file.file.read(covenants.MAX_TB_BYTES + 1)
        row = covenants.upload_trial_balance(s, p, document_id, period_end, file.filename or "", data)
        lines = dict((r.id, n) for r, n in covenants.list_trial_balances(s, p.tenant_id, document_id))[row.id]
        return TrialBalanceOut(
            id=row.id, period_end=row.period_end, filename=row.filename, sha256=row.sha256,
            uploaded_by=row.uploaded_by, lines=lines,
        )

    @app.get("/documents/{document_id}/trial-balances", response_model=list[TrialBalanceOut])
    def trial_balances(document_id: str, p: Auth, s: Db) -> list[TrialBalanceOut]:
        return [
            TrialBalanceOut(
                id=r.id, period_end=r.period_end, filename=r.filename, sha256=r.sha256, uploaded_by=r.uploaded_by,
                lines=n,
            )
            for r, n in covenants.list_trial_balances(s, p.tenant_id, document_id)
        ]

    @app.delete("/trial-balances/{trial_balance_id}", status_code=204)
    def delete_trial_balance(trial_balance_id: str, p: Auth, s: Db) -> Response:
        covenants.delete_trial_balance(s, p, trial_balance_id)
        return Response(status_code=204)

    @app.get("/documents/{document_id}/covenants", response_model=CovenantsOut)
    def covenant_results(document_id: str, p: Auth, s: Db, as_of: date | None = None) -> CovenantsOut:
        as_of = as_of or date.today()
        r = covenants.covenant_run(s, p.tenant_id, document_id, as_of)
        field_ids = {f.name: f.id for f in r.fields}

        def periods_used(period_end: date) -> list[str]:
            return [r.trial_balances[d].id for d in quarters_back(period_end, 4) if d in r.trial_balances]

        return CovenantsOut(
            as_of=as_of,
            terms=CovenantTermsOut(
                first_test_date=r.terms.first_test_date, reporting_days=r.terms.reporting_days,
                cure_days=r.terms.cure_days, warning_headroom=r.terms.warning_headroom,
                cure_day_basis="business" if r.terms.cure_business_days else "calendar",
                cure_runs_from="delivery" if r.terms.cure_from_delivery else "due date",
                springing_usage=r.terms.springing_usage, revolver_commitment=r.terms.revolver_commitment,
            ),
            test_dates=[
                TestDateOut(
                    period_end=t.period_end,
                    certificate_due=t.certificate_due,
                    cure_deadline=t.cure_deadline,
                    cure_pending=t.cure_pending,
                    status=covenants.status_of(t, as_of),
                    missing=t.missing,
                    trial_balance_ids=periods_used(t.period_end),
                    results=[
                        CovenantResultOut(
                            kind=res.covenant.kind.value,
                            field_id=field_ids[r.terms.field_for(res.covenant.kind)],
                            threshold=res.covenant.threshold,
                            status=res.status,
                            value=None if res.value is None else round(res.value, 2 if res.covenant.kind == "min_liquidity" else 4),
                            headroom=None if res.headroom is None else round(res.headroom, 4),
                            workings=[WorkingOut(label=w.label, amount=w.amount) for w in res.workings],
                            note=res.note,
                        )
                        for res in t.results
                    ],
                )
                for t in r.test_dates
            ],
            alerts=[AlertOut(severity=a.severity, on=a.on, message=a.message) for a in covenants.alerts(r.test_dates, as_of)],
            inputs=[FieldOut.of(f) for f in r.fields],
            period_inputs=[PeriodInputOut.of(x) for x in r.period_inputs],
        )

    @app.put("/documents/{document_id}/covenants/periods/{period_end}", response_model=PeriodInputOut)
    def save_period_input(document_id: str, period_end: date, body: PeriodInputIn, p: Auth, s: Db) -> PeriodInputOut:
        return PeriodInputOut.of(covenants.save_period_input(
            s, p, document_id, period_end, body.revolver_drawn, body.delivered_on, body.basis))

    @app.post("/documents/{document_id}/covenants/periods/{period_end}/confirm", response_model=PeriodInputOut)
    def confirm_period_input(document_id: str, period_end: date, p: Auth, s: Db) -> PeriodInputOut:
        return PeriodInputOut.of(covenants.confirm_period_input(s, p, document_id, period_end))

    tax_routes.add_routes(app, Auth, Db, store)
    etims.add_routes(app, Auth, Db)
    variance.add_routes(app, Auth, Db)
    statements.add_routes(app, Auth, Db)
    memo.add_routes(app, Auth, Db, store)
    ledger.add_routes(app, Auth, Db, store)

    app.state.session_factory, app.state.store, app.state.extractor = factory, store, extractor
    return app


def __getattr__(name: str):  # lazy so importing this module (e.g. in tests) never touches the default database
    if name == "app":
        return create_app()
    raise AttributeError(name)
