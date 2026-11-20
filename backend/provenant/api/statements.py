"""M-Pesa and bank statement use cases and routes: upload, consolidate, categorise (suggest, then a person
confirms), and compare categorised receipts with revenue in the trial balance (ADR 0007)."""
import hashlib
from datetime import date
from decimal import Decimal

from fastapi import FastAPI, File, Form, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from provenant.api.auth import Principal
from provenant.api.db import StatementCategoryRow, StatementRow
from provenant.api.services import Conflict, Invalid, NotFound, require
from provenant.api.tax import ANY_ROLE, get_client, lead_schedule, mapped_accounts
from provenant.tax import statements as st

READERS = {"mpesa": st.read_mpesa, "bank": st.read_bank}


def _statements(s: Session, tenant_id: str, client_id: str, year_end: date) -> list[StatementRow]:
    return list(s.scalars(select(StatementRow).where(
        StatementRow.tenant_id == tenant_id, StatementRow.client_id == client_id, StatementRow.year_end == year_end,
    ).order_by(StatementRow.created_at)))


def upload(s: Session, p: Principal, client_id: str, year_end: date, kind: str, label: str, filename: str,
           data: bytes) -> StatementRow:
    require(p, *ANY_ROLE)
    get_client(s, p.tenant_id, client_id)
    if kind not in READERS:
        raise Invalid("kind must be mpesa or bank")
    label = " ".join(label.split())[:120]
    if len(label) < 3:
        raise Invalid("name the account, e.g. 'M-Pesa Till 123456' or 'Equity Bank 0123'")
    try:
        READERS[kind](data, label)
    except ValueError as exc:
        raise Invalid(str(exc)) from exc
    row = StatementRow(tenant_id=p.tenant_id, client_id=client_id, year_end=year_end, kind=kind, label=label,
                       filename=filename[:255] or f"{kind}.csv", sha256=hashlib.sha256(data).hexdigest(),
                       content=data.decode("utf-8-sig"), uploaded_by=p.user)
    s.add(row)
    try:
        s.commit()
    except IntegrityError as exc:
        s.rollback()
        raise Conflict("this exact file is already uploaded for the year") from exc
    return row


def remove(s: Session, p: Principal, client_id: str, statement_id: str) -> None:
    require(p, *ANY_ROLE)
    row = s.scalar(select(StatementRow).where(StatementRow.tenant_id == p.tenant_id,
                                              StatementRow.client_id == client_id, StatementRow.id == statement_id))
    if row is None:
        raise NotFound("statement not found")
    s.delete(row)
    s.commit()


def set_category(s: Session, p: Principal, client_id: str, year_end: date, keys: list[str], category: str | None) -> int:
    """Confirm a category for movements (or clear it with None). Returns how many changed."""
    require(p, *ANY_ROLE)
    get_client(s, p.tenant_id, client_id)
    if category is not None and category not in st.CATEGORIES:
        raise Invalid(f"category must be one of {', '.join(st.CATEGORIES)}")
    existing = {r.txn_key: r for r in s.scalars(select(StatementCategoryRow).where(
        StatementCategoryRow.tenant_id == p.tenant_id, StatementCategoryRow.client_id == client_id,
        StatementCategoryRow.year_end == year_end, StatementCategoryRow.txn_key.in_(keys)))}
    for key in keys:
        row = existing.get(key)
        if category is None:
            if row is not None:
                s.delete(row)
            continue
        if row is None:
            row = StatementCategoryRow(tenant_id=p.tenant_id, client_id=client_id, year_end=year_end, txn_key=key[:200])
            s.add(row)
        row.category, row.decided_by = category, p.user
    s.commit()
    return len(keys)


def run(s: Session, tenant_id: str, client_id: str, year_end: date):
    get_client(s, tenant_id, client_id)
    rows = _statements(s, tenant_id, client_id, year_end)
    parsed = [READERS[r.kind](r.content.encode(), r.label) for r in rows]
    result = st.consolidate(parsed)
    confirmed = {r.txn_key: (r.category, r.decided_by) for r in s.scalars(select(StatementCategoryRow).where(
        StatementCategoryRow.tenant_id == tenant_id, StatementCategoryRow.client_id == client_id,
        StatementCategoryRow.year_end == year_end))}
    items = st.categorise(result, confirmed)
    return rows, parsed, result, items


def confirm_suggestions(s: Session, p: Principal, client_id: str, year_end: date) -> int:
    require(p, *ANY_ROLE)
    _, _, _, items = run(s, p.tenant_id, client_id, year_end)
    suggested = [c for c in items if c.status == "suggested"]
    for c in suggested:
        s.add(StatementCategoryRow(tenant_id=p.tenant_id, client_id=client_id, year_end=year_end,
                                   txn_key=c.txn.key[:200], category=c.category, decided_by=p.user))
    s.commit()
    return len(suggested)


# --- HTTP -------------------------------------------------------------------------------------------------------


class StatementOut(BaseModel):
    id: str
    kind: str
    label: str
    filename: str
    uploaded_by: str
    movements: int
    money_in: Decimal
    money_out: Decimal
    first_date: date | None
    last_date: date | None


class TxnOut(BaseModel):
    key: str
    source: str
    row: int
    date: date
    reference: str
    description: str
    counterparty: str
    amount: Decimal
    category: str | None
    status: str
    rule: str


class BreakOut(BaseModel):
    source: str
    row: int
    expected: Decimal
    stated: Decimal
    gap: Decimal


class StatementsOut(BaseModel):
    statements: list[StatementOut]
    categories: dict[str, str]  # the vocabulary, so the UI offers the same choices the server accepts
    transactions: list[TxnOut]
    totals: dict[str, tuple[Decimal, Decimal]]  # category -> (money in, money out)
    duplicates: int
    transfers: int
    breaks: list[BreakOut]
    revenue: Decimal | None  # revenue in the trial balance, beside confirmed sales receipts
    confirmed_sales_receipts: Decimal


class CategoryIn(BaseModel):
    keys: list[str] = Field(max_length=5000)
    category: str | None = Field(default=None, max_length=32)


def _out(s: Session, tenant_id: str, client_id: str, year_end: date) -> StatementsOut:
    rows, parsed, result, items = run(s, tenant_id, client_id, year_end)
    try:
        lead = {key: amount for key, amount, _ in lead_schedule(mapped_accounts(s, tenant_id, client_id, year_end))}
        revenue = lead.get("revenue")
    except NotFound:
        revenue = None  # no trial balance for the year yet
    return StatementsOut(
        statements=[
            StatementOut(id=r.id, kind=r.kind, label=r.label, filename=r.filename, uploaded_by=r.uploaded_by,
                         movements=len(txns), money_in=sum((t.amount for t in txns if t.amount > 0), Decimal(0)),
                         money_out=-sum((t.amount for t in txns if t.amount < 0), Decimal(0)),
                         first_date=min((t.date for t in txns), default=None),
                         last_date=max((t.date for t in txns), default=None))
            for r, txns in zip(rows, parsed)
        ],
        categories=st.CATEGORIES,
        transactions=[TxnOut(key=c.txn.key, source=c.txn.source, row=c.txn.row, date=c.txn.date,
                             reference=c.txn.reference, description=c.txn.description, counterparty=c.txn.counterparty,
                             amount=c.txn.amount, category=c.category, status=c.status, rule=c.rule) for c in items],
        totals=st.totals(items), duplicates=len(result.duplicates), transfers=len(result.transfers),
        breaks=[BreakOut(source=b.source, row=b.row, expected=b.expected, stated=b.stated, gap=b.gap)
                for b in result.breaks],
        revenue=revenue,
        confirmed_sales_receipts=sum((c.txn.amount for c in items
                                      if c.status == "confirmed" and c.category == "sales_receipt"), Decimal(0)),
    )


def add_routes(app: FastAPI, Auth, Db) -> None:  # noqa: N803 - the app's Annotated dependency types
    base = "/clients/{client_id}/years/{year_end}/statements"

    @app.get(base, response_model=StatementsOut)
    def get_statements(client_id: str, year_end: date, p: Auth, s: Db) -> StatementsOut:
        return _out(s, p.tenant_id, client_id, year_end)

    @app.post(base, response_model=StatementsOut, status_code=201)
    def upload_statement(client_id: str, year_end: date, p: Auth, s: Db, file: UploadFile = File(),
                         kind: str = Form(), label: str = Form()) -> StatementsOut:
        upload(s, p, client_id, year_end, kind, label, file.filename or "", file.file.read(st.MAX_BYTES + 1))
        return _out(s, p.tenant_id, client_id, year_end)

    @app.delete(base + "/{statement_id}", response_model=StatementsOut)
    def delete_statement(client_id: str, year_end: date, statement_id: str, p: Auth, s: Db) -> StatementsOut:
        remove(s, p, client_id, statement_id)
        return _out(s, p.tenant_id, client_id, year_end)

    @app.put(base + "/categories", response_model=StatementsOut)
    def categories(client_id: str, year_end: date, body: CategoryIn, p: Auth, s: Db) -> StatementsOut:
        set_category(s, p, client_id, year_end, body.keys, body.category)
        return _out(s, p.tenant_id, client_id, year_end)

    @app.post(base + "/confirm-suggestions", response_model=StatementsOut)
    def confirm(client_id: str, year_end: date, p: Auth, s: Db) -> StatementsOut:
        confirm_suggestions(s, p, client_id, year_end)
        return _out(s, p.tenant_id, client_id, year_end)
