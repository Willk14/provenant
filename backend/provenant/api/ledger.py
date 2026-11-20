"""General ledger use cases and routes: upload (streamed to disk, normalised to Parquet), full-population journal
entry tests, and decisions on flagged entries under maker-checker (ADR 0009)."""
import hashlib
import tempfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from provenant.analytics import journal
from provenant.api.auth import PARTNER, REVIEWER, Principal
from provenant.api.db import GeneralLedgerRow, JournalExceptionRow
from provenant.api.services import Forbidden, Invalid, NotFound, require
from provenant.api.storage import BlobStore
from provenant.api.tax import ANY_ROLE, CENT, get_client, get_trial_balance, mapped_accounts, year_start
from provenant.core.money import to_decimal
from provenant.tax.mapping import FS_LINES, Statement

MAX_BYTES = 500 * 1024 * 1024
CHUNK = 1024 * 1024


def _ledger(s: Session, tenant_id: str, client_id: str, year_end: date) -> GeneralLedgerRow | None:
    return s.scalar(select(GeneralLedgerRow).where(GeneralLedgerRow.tenant_id == tenant_id,
                                                   GeneralLedgerRow.client_id == client_id,
                                                   GeneralLedgerRow.year_end == year_end))


def _limit(text: str | None) -> int | None:
    if not text or not text.strip():
        return None
    try:
        value = to_decimal(text)
    except (ValueError, TypeError) as exc:
        raise Invalid(f"approval limit: {exc}") from exc
    if value <= 0:
        raise Invalid("approval limit must be positive")
    return int(value / CENT)


def upload(s: Session, p: Principal, store: BlobStore, client_id: str, year_end: date, filename: str, stream,
           approval_limit: str | None = None) -> GeneralLedgerRow:
    """Stream the CSV to disk (never wholly in memory), check and normalise it with DuckDB, keep both files."""
    require(p, *ANY_ROLE)
    get_trial_balance(s, p.tenant_id, client_id, year_end)
    limit = _limit(approval_limit)
    with tempfile.TemporaryDirectory(prefix="provenant-gl-") as tmp:
        csv_path, parquet_path = Path(tmp) / "gl.csv", Path(tmp) / "gl.parquet"
        digest, size = hashlib.sha256(), 0
        with csv_path.open("wb") as out:
            while chunk := stream.read(CHUNK):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise Invalid(f"the general ledger exceeds {MAX_BYTES // (1024 * 1024)} MB")
                digest.update(chunk)
                out.write(chunk)
        try:
            lines = journal.load(csv_path, parquet_path)
        except journal.LedgerError as exc:
            raise Invalid(str(exc)) from exc
        parquet_sha = hashlib.sha256(parquet_path.read_bytes()).hexdigest()  # compressed: small even for big ledgers
        store.put_file(digest.hexdigest(), csv_path)
        store.put_file(parquet_sha, parquet_path)
    row = _ledger(s, p.tenant_id, client_id, year_end)
    if row is None:
        row = GeneralLedgerRow(tenant_id=p.tenant_id, client_id=client_id, year_end=year_end)
        s.add(row)
    row.filename, row.csv_sha256, row.parquet_sha256 = filename[:255] or "general-ledger.csv", digest.hexdigest(), parquet_sha
    row.lines, row.approval_limit_cents, row.uploaded_by = lines, limit, p.user
    s.commit()
    return row


def set_limit(s: Session, p: Principal, client_id: str, year_end: date, approval_limit: str | None) -> None:
    require(p, *ANY_ROLE)
    row = _ledger(s, p.tenant_id, client_id, year_end)
    if row is None:
        raise NotFound("no general ledger for this year")
    row.approval_limit_cents = _limit(approval_limit)
    s.commit()


def decide(s: Session, p: Principal, client_id: str, year_end: date, entry_id: str, test: str, status: str,
           reason: str) -> None:
    """Clear (a reviewer or partner, not the person who uploaded the ledger), escalate (anyone), or reopen."""
    require(p, *ANY_ROLE)
    ledger = _ledger(s, p.tenant_id, client_id, year_end)
    if ledger is None:
        raise NotFound("no general ledger for this year")
    if test not in journal.TESTS:
        raise Invalid("unknown test")
    if status == "cleared":
        require(p, REVIEWER, PARTNER)
        if p.user == ledger.uploaded_by:
            raise Forbidden("you uploaded this ledger: someone else must clear its exceptions")
    reason = " ".join(reason.split())
    if status in ("cleared", "escalated") and len(reason) < 10:
        raise Invalid("give the reason in a sentence (what was checked, or why it is escalated)")
    row = s.scalar(select(JournalExceptionRow).where(
        JournalExceptionRow.tenant_id == p.tenant_id, JournalExceptionRow.client_id == client_id,
        JournalExceptionRow.year_end == year_end, JournalExceptionRow.entry_id == entry_id,
        JournalExceptionRow.test == test))
    if status == "open":
        if row is not None:
            s.delete(row)
    else:
        if row is None:
            row = JournalExceptionRow(tenant_id=p.tenant_id, client_id=client_id, year_end=year_end,
                                      entry_id=entry_id[:64], test=test)
            s.add(row)
        row.status, row.reason, row.decided_by = status, reason[:1000], p.user
    s.commit()


def analysis(s: Session, store: BlobStore, tenant_id: str, client_id: str, year_end: date):
    get_client(s, tenant_id, client_id)
    ledger = _ledger(s, tenant_id, client_id, year_end)
    if ledger is None:
        return None, None, {}
    pl = {a.line.code: Decimal(a.line.balance_cents) * CENT for a in mapped_accounts(s, tenant_id, client_id, year_end)
          if a.mapping.fs_line and FS_LINES[a.mapping.fs_line].statement is Statement.PROFIT_OR_LOSS}
    limit = Decimal(ledger.approval_limit_cents) * CENT if ledger.approval_limit_cents else None
    result = journal.analyse(store.local_path(ledger.parquet_sha256), year_start(year_end), year_end,
                             approval_limit=limit, pl_balances=pl)
    decisions = {(d.entry_id, d.test): d for d in s.scalars(select(JournalExceptionRow).where(
        JournalExceptionRow.tenant_id == tenant_id, JournalExceptionRow.client_id == client_id,
        JournalExceptionRow.year_end == year_end))}
    return ledger, result, decisions


# --- HTTP -------------------------------------------------------------------------------------------------------


class DecisionOut(BaseModel):
    status: str
    reason: str
    decided_by: str
    decided_at: datetime


class FlagOut(BaseModel):
    entry_id: str
    value: Decimal
    detail: str
    decision: DecisionOut | None


class TestOut(BaseModel):
    key: str
    title: str
    description: str
    applies: bool
    count: int
    value: Decimal
    open: int  # flagged entries in the sample not yet cleared
    sample: list[FlagOut]


class RiskOut(BaseModel):
    entry_id: str
    value: Decimal
    tests: list[str]
    cleared: int


class LedgerOut(BaseModel):
    uploaded: bool
    filename: str | None = None
    uploaded_by: str | None = None
    test_version: str = journal.TEST_VERSION
    approval_limit: Decimal | None = None
    lines: int = 0
    entries: int = 0
    debits: Decimal = Decimal(0)
    first_date: date | None = None
    last_date: date | None = None
    tests: list[TestOut] = []
    benford: dict | None = None
    reconciliation: list[dict] = []
    risk: list[RiskOut] = []


class LineOut(BaseModel):
    entry_id: str
    row: int
    date: date
    posted_at: datetime | None
    account_code: str
    description: str
    user: str | None
    source: str
    amount: Decimal


class DecisionIn(BaseModel):
    entry_id: str = Field(max_length=64)
    test: str = Field(max_length=32)
    status: str = Field(pattern="^(cleared|escalated|open)$")
    reason: str = Field(default="", max_length=1000)


class LimitIn(BaseModel):
    approval_limit: str | None = Field(default=None, max_length=32)


def _decision(d) -> DecisionOut | None:
    return DecisionOut(status=d.status, reason=d.reason, decided_by=d.decided_by, decided_at=d.decided_at) if d else None


def _out(ledger, a: journal.Analysis | None, decisions) -> LedgerOut:
    if ledger is None or a is None:
        return LedgerOut(uploaded=False)
    tests = []
    for t in a.tests:
        sample = [FlagOut(entry_id=f.entry_id, value=f.value, detail=f.detail,
                          decision=_decision(decisions.get((f.entry_id, t.key)))) for f in t.sample]
        tests.append(TestOut(key=t.key, title=t.title, description=t.description, applies=t.applies, count=t.count,
                             value=t.value, open=sum(1 for f in sample if not f.decision or f.decision.status != "cleared"),
                             sample=sample))
    b = a.benford
    return LedgerOut(
        uploaded=True, filename=ledger.filename, uploaded_by=ledger.uploaded_by,
        approval_limit=Decimal(ledger.approval_limit_cents) * CENT if ledger.approval_limit_cents else None,
        lines=a.lines, entries=a.entries, debits=a.debits, first_date=a.first_date, last_date=a.last_date,
        tests=tests,
        benford={"lines": b.lines, "observed": list(b.observed), "expected": list(b.expected), "mad": b.mad,
                 "conformity": b.conformity} if b else None,
        reconciliation=[{"account_code": r.account_code, "ledger": r.ledger, "trial_balance": r.trial_balance,
                         "difference": r.difference} for r in a.reconciliation],
        risk=[RiskOut(entry_id=e, value=v, tests=list(ts),
                      cleared=sum(1 for t in ts if (d := decisions.get((e, t))) and d.status == "cleared"))
              for e, v, ts in a.risk],
    )


def add_routes(app: FastAPI, Auth, Db, store: BlobStore) -> None:  # noqa: N803 - the app's Annotated dependency types
    base = "/clients/{client_id}/years/{year_end}/ledger"

    def view(s, tenant_id, client_id, year_end) -> LedgerOut:
        return _out(*analysis(s, store, tenant_id, client_id, year_end))

    @app.get(base, response_model=LedgerOut)
    def get_ledger(client_id: str, year_end: date, p: Auth, s: Db) -> LedgerOut:
        return view(s, p.tenant_id, client_id, year_end)

    @app.post(base, response_model=LedgerOut, status_code=201)
    def upload_ledger(client_id: str, year_end: date, p: Auth, s: Db, file: UploadFile = File(),
                      approval_limit: str = Form("")) -> LedgerOut:
        upload(s, p, store, client_id, year_end, file.filename or "", file.file, approval_limit)
        return view(s, p.tenant_id, client_id, year_end)

    @app.put(base + "/settings", response_model=LedgerOut)
    def settings(client_id: str, year_end: date, body: LimitIn, p: Auth, s: Db) -> LedgerOut:
        set_limit(s, p, client_id, year_end, body.approval_limit)
        return view(s, p.tenant_id, client_id, year_end)

    @app.put(base + "/exceptions", response_model=LedgerOut)
    def exception(client_id: str, year_end: date, body: DecisionIn, p: Auth, s: Db) -> LedgerOut:
        decide(s, p, client_id, year_end, body.entry_id, body.test, body.status, body.reason)
        return view(s, p.tenant_id, client_id, year_end)

    @app.get(base + "/entries/{entry_id}", response_model=list[LineOut])
    def entry(client_id: str, year_end: date, entry_id: str, p: Auth, s: Db) -> list[LineOut]:
        get_client(s, p.tenant_id, client_id)
        ledger = _ledger(s, p.tenant_id, client_id, year_end)
        if ledger is None:
            raise NotFound("no general ledger for this year")
        rows = journal.entry_lines(store.local_path(ledger.parquet_sha256), [entry_id])
        if not rows:
            raise NotFound("no such entry")
        return [LineOut(entry_id=r[0], row=r[1], date=r[2], posted_at=r[3], account_code=r[4], description=r[5],
                        user=r[6], source=r[7], amount=r[8]) for r in rows]
