"""Client trust account: cashbook, client ledgers and the three-way reconciliation (ADR 0018).

Provenant records and checks. It never moves money: a payment out of the trust account is made at the bank by a person.
"""
import csv
import hashlib
import io
import json
from datetime import date, datetime, timedelta, UTC
from decimal import Decimal
from typing import Annotated

from fastapi import FastAPI, File, Form, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from provenant.api.auth import PARTNER, PREPARER, REVIEWER, Principal
from provenant.api.db import MatterRow, TrustAccountRow, TrustEntryRow, TrustReconciliationRow
from provenant.api.services import MAX_UPLOAD_BYTES, Conflict, Forbidden, Invalid, NotFound, require
from provenant.core.money import round_money, to_decimal
from provenant.practice import trust as engine
from provenant.tax.statements import read_bank

ANY = (PREPARER, REVIEWER, PARTNER)
HUNDRED = Decimal(100)


def _cents(amount: Decimal) -> int:
    return int(round_money(amount) * HUNDRED)


def _amount(cents: int) -> Decimal:
    return Decimal(cents) / HUNDRED


def get_account(s: Session, tenant_id: str, account_id: str) -> TrustAccountRow:
    row = s.scalar(select(TrustAccountRow).where(TrustAccountRow.tenant_id == tenant_id, TrustAccountRow.id == account_id))
    if row is None:
        raise NotFound("trust account not found")
    return row


def _matter_ids(s: Session, tenant_id: str) -> dict[str, str]:
    return {m.reference.lower(): m.id for m in s.scalars(select(MatterRow).where(MatterRow.tenant_id == tenant_id))}


def add_entry(s: Session, p: Principal, account_id: str, on: date, amount: str, reference: str, description: str,
              matter_id: str | None) -> TrustEntryRow:
    require(p, *ANY)
    get_account(s, p.tenant_id, account_id)
    try:
        value = to_decimal(amount)
    except (ValueError, TypeError) as exc:
        raise Invalid(f"amount: {exc}") from exc
    if value == 0:
        raise Invalid("an entry needs a non-zero amount: receipts positive, payments negative")
    if matter_id and matter_id not in _matter_ids(s, p.tenant_id).values():
        raise NotFound("matter not found")
    row = TrustEntryRow(tenant_id=p.tenant_id, account_id=account_id, entry_date=on, matter_id=matter_id or None,
                        amount_cents=_cents(value), reference=reference.strip()[:64],
                        description=" ".join(description.split())[:255] or "-", entered_by=p.user)
    s.add(row)
    s.commit()
    return row


def import_entries(s: Session, p: Principal, account_id: str, data: bytes) -> int:
    """A cashbook CSV: date, amount (receipts positive, payments negative), reference, description and the matter
    reference each line belongs to. Lines with no matter stay unallocated, which the reconciliation flags."""
    require(p, *ANY)
    get_account(s, p.tenant_id, account_id)
    try:
        reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
    except UnicodeDecodeError as exc:
        raise Invalid("the cashbook must be a UTF-8 CSV file") from exc
    fields = {f.strip().lower(): f for f in reader.fieldnames or []}
    if not {"date", "amount"} <= set(fields):
        raise Invalid("the cashbook CSV needs date and amount columns (and reference, description, matter)")
    matters, rows = _matter_ids(s, p.tenant_id), []
    for number, raw in enumerate(reader, start=2):
        get = lambda key: (raw.get(fields[key]) or "").strip() if key in fields else ""  # noqa: E731
        try:
            on = date.fromisoformat(get("date"))
            value = to_decimal(get("amount"))
        except (ValueError, TypeError) as exc:
            raise Invalid(f"row {number}: {exc}") from exc
        ref = get("matter").lower()
        if ref and ref not in matters:
            raise Invalid(f"row {number}: no matter {get('matter')!r}")
        rows.append(TrustEntryRow(tenant_id=p.tenant_id, account_id=account_id, entry_date=on,
                                  matter_id=matters.get(ref), amount_cents=_cents(value),
                                  reference=get("reference")[:64], description=get("description")[:255] or "-",
                                  entered_by=p.user))
    if not rows:
        raise Invalid("the cashbook has no lines")
    s.add_all(rows)
    s.commit()
    return len(rows)


def _entries(s: Session, tenant_id: str, account_id: str) -> list[TrustEntryRow]:
    return list(s.scalars(select(TrustEntryRow).where(TrustEntryRow.tenant_id == tenant_id,
                                                      TrustEntryRow.account_id == account_id)
                          .order_by(TrustEntryRow.entry_date, TrustEntryRow.created_at)))


def _to_engine(row: TrustEntryRow) -> engine.Entry:
    return engine.Entry(row.id, row.entry_date, _amount(row.amount_cents), row.reference, row.matter_id)


def _workpaper(result: engine.Reconciliation, references: dict[str, str]) -> dict:
    money = lambda d: f"{round_money(d):.2f}"  # noqa: E731
    return {
        "as_of": result.as_of.isoformat(), "bank_balance": money(result.bank_balance),
        "adjusted_bank": money(result.adjusted_bank), "cashbook": money(result.cashbook),
        "ledgers_total": money(result.ledgers_total), "balanced": result.balanced,
        "outstanding": [{"id": e.id, "date": e.on.isoformat(), "amount": money(e.amount), "reference": e.reference,
                         "matter": references.get(e.matter or "", None)} for e in result.outstanding],
        "unrecorded": [{"date": x.on.isoformat(), "amount": money(x.amount), "reference": x.reference,
                        "description": x.description} for x in result.unrecorded],
        "ledgers": [{"matter": references.get(x.matter, x.matter), "balance": money(x.balance),
                     "last_movement": x.last_movement.isoformat()} for x in result.ledgers],
        "alerts": [{"kind": a.kind, "severity": a.severity, "message": a.message, "amount": money(a.amount),
                    "matter": references.get(a.matter or "", None)} for a in result.alerts],
        "matched": len(result.matched),
    }


def prepare(s: Session, p: Principal, account_id: str, as_of: date, bank_balance: str, statement: bytes) -> TrustReconciliationRow:
    require(p, *ANY)
    account = get_account(s, p.tenant_id, account_id)
    try:
        balance = to_decimal(bank_balance)
        txns = read_bank(statement, account.name)
    except (ValueError, TypeError) as exc:
        raise Invalid(str(exc)) from exc
    lines = [engine.BankLine(f"b{t.row}", t.date, t.amount, t.reference, t.description) for t in txns]
    if not lines:
        raise Invalid("the bank statement has no lines")
    start = min(x.on for x in lines)
    entries = _entries(s, p.tenant_id, account_id)
    previous = s.scalar(select(TrustReconciliationRow).where(
        TrustReconciliationRow.tenant_id == p.tenant_id, TrustReconciliationRow.account_id == account_id,
        TrustReconciliationRow.approved_by.is_not(None), TrustReconciliationRow.as_of < as_of)
        .order_by(TrustReconciliationRow.as_of.desc()).limit(1))
    carried = {o["id"] for o in json.loads(previous.result)["outstanding"]} if previous else set()
    pool = [_to_engine(e) for e in entries if start <= e.entry_date <= as_of or e.id in carried]
    dormant = timedelta(days=account.dormant_days) if account.dormant_days else None
    result = engine.reconcile(as_of, balance, [_to_engine(e) for e in entries], pool, lines, dormant)
    references = {m.id: m.reference for m in s.scalars(select(MatterRow).where(MatterRow.tenant_id == p.tenant_id))}
    row = TrustReconciliationRow(tenant_id=p.tenant_id, account_id=account_id, as_of=as_of, period_start=start,
                                 bank_balance_cents=_cents(balance), statement_sha256=hashlib.sha256(statement).hexdigest(),
                                 result=json.dumps(_workpaper(result, references)), balanced=result.balanced,
                                 prepared_by=p.user)
    s.add(row)
    s.commit()
    return row


def approve(s: Session, p: Principal, account_id: str, reconciliation_id: str) -> TrustReconciliationRow:
    """A reviewer or partner who did not prepare it signs off. A reconciliation with hard alerts cannot be approved:
    the breaks are fixed with cashbook entries first, then it is prepared again."""
    require(p, REVIEWER, PARTNER)
    row = s.scalar(select(TrustReconciliationRow).where(TrustReconciliationRow.tenant_id == p.tenant_id,
                                                        TrustReconciliationRow.account_id == account_id,
                                                        TrustReconciliationRow.id == reconciliation_id))
    if row is None:
        raise NotFound("reconciliation not found")
    if row.approved_by:
        raise Conflict(f"already approved by {row.approved_by}")
    if p.user == row.prepared_by:
        raise Forbidden("maker-checker: someone other than the preparer must approve it")
    if not row.balanced:
        raise Conflict("resolve the hard alerts and prepare it again before approving")
    row.approved_by, row.approved_at = p.user, datetime.now(UTC)
    s.commit()
    return row


class AccountIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    bank: str = Field(min_length=2, max_length=120)
    dormant_days: int | None = Field(default=None, ge=30, le=3650)
    dormant_basis: str | None = Field(default=None, max_length=300)


class EntryIn(BaseModel):
    entry_date: date
    amount: str = Field(max_length=32)
    reference: str = Field(default="", max_length=64)
    description: str = Field(default="", max_length=255)
    matter_id: str | None = Field(default=None, max_length=36)


def _reconciliation_out(r: TrustReconciliationRow) -> dict:
    return {"id": r.id, "as_of": r.as_of.isoformat(), "period_start": r.period_start.isoformat(),
            "balanced": r.balanced, "prepared_by": r.prepared_by, "prepared_at": r.prepared_at.isoformat(),
            "approved_by": r.approved_by, "approved_at": r.approved_at.isoformat() if r.approved_at else None,
            "workpaper": json.loads(r.result)}


def add_routes(app: FastAPI, Auth, Db) -> None:  # noqa: N803 - FastAPI dependency aliases
    @app.get("/trust/accounts")
    def accounts(p: Auth, s: Db) -> list[dict]:
        out = []
        for a in s.scalars(select(TrustAccountRow).where(TrustAccountRow.tenant_id == p.tenant_id).order_by(TrustAccountRow.name)):
            entries = _entries(s, p.tenant_id, a.id)
            out.append({"id": a.id, "name": a.name, "bank": a.bank, "dormant_days": a.dormant_days,
                        "dormant_basis": a.dormant_basis, "entries": len(entries),
                        "cashbook": f"{_amount(sum(e.amount_cents for e in entries)):.2f}"})
        return out

    @app.post("/trust/accounts", status_code=201)
    def create_account(body: AccountIn, p: Auth, s: Db) -> dict:
        require(p, PARTNER)
        if body.dormant_days and not (body.dormant_basis or "").strip():
            raise Invalid("state the basis for the dormancy period")
        row = TrustAccountRow(tenant_id=p.tenant_id, name=body.name.strip(), bank=body.bank.strip(),
                              dormant_days=body.dormant_days, dormant_basis=(body.dormant_basis or "").strip() or None,
                              created_by=p.user)
        s.add(row)
        s.commit()
        return {"id": row.id, "name": row.name}

    @app.get("/trust/accounts/{account_id}/entries")
    def entries(account_id: str, p: Auth, s: Db) -> list[dict]:
        get_account(s, p.tenant_id, account_id)
        references = {m.id: m.reference for m in s.scalars(select(MatterRow).where(MatterRow.tenant_id == p.tenant_id))}
        return [{"id": e.id, "date": e.entry_date.isoformat(), "amount": f"{_amount(e.amount_cents):.2f}",
                 "reference": e.reference, "description": e.description, "matter": references.get(e.matter_id or ""),
                 "entered_by": e.entered_by} for e in _entries(s, p.tenant_id, account_id)]

    @app.post("/trust/accounts/{account_id}/entries", status_code=201)
    def post_entry(account_id: str, body: EntryIn, p: Auth, s: Db) -> dict:
        row = add_entry(s, p, account_id, body.entry_date, body.amount, body.reference, body.description, body.matter_id)
        return {"id": row.id}

    @app.post("/trust/accounts/{account_id}/entries/import", status_code=201)
    def import_cashbook(account_id: str, p: Auth, s: Db, file: Annotated[UploadFile, File()]) -> dict:
        return {"imported": import_entries(s, p, account_id, file.file.read(MAX_UPLOAD_BYTES + 1))}

    @app.get("/trust/accounts/{account_id}/reconciliations")
    def reconciliations(account_id: str, p: Auth, s: Db) -> list[dict]:
        get_account(s, p.tenant_id, account_id)
        rows = s.scalars(select(TrustReconciliationRow).where(TrustReconciliationRow.tenant_id == p.tenant_id,
                                                              TrustReconciliationRow.account_id == account_id)
                         .order_by(TrustReconciliationRow.prepared_at.desc()))
        return [_reconciliation_out(r) for r in rows]

    @app.post("/trust/accounts/{account_id}/reconciliations", status_code=201)
    def post_reconciliation(account_id: str, p: Auth, s: Db, statement: Annotated[UploadFile, File()],
                            as_of: Annotated[date, Form()], bank_balance: Annotated[str, Form(max_length=32)]) -> dict:
        data = statement.file.read(MAX_UPLOAD_BYTES + 1)
        return _reconciliation_out(prepare(s, p, account_id, as_of, bank_balance, data))

    @app.post("/trust/accounts/{account_id}/reconciliations/{reconciliation_id}/approve")
    def post_approve(account_id: str, reconciliation_id: str, p: Auth, s: Db) -> dict:
        return _reconciliation_out(approve(s, p, account_id, reconciliation_id))
