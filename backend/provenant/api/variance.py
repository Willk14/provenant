"""Variance checks use cases and routes: upload filed returns, run the checks, record explanations (ADR 0007)."""
import hashlib
from datetime import date, datetime
from decimal import Decimal

from fastapi import FastAPI, File, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from provenant.api.auth import Principal
from provenant.api.db import ComplianceReturnRow, PurchaseLedgerRow, PurchaseLineRow, VarianceExplanationRow
from provenant.api.services import Invalid, require
from provenant.api.tax import ANY_ROLE, CENT, get_client, get_trial_balance, lead_schedule, mapped_accounts, year_start
from provenant.tax import variance

READERS = {"vat": variance.read_vat, "paye": variance.read_paye, "wht": variance.read_wht}
CHECK_FOR = {"vat": "vat_turnover", "paye": "paye", "wht": "wht"}
CHECK_TITLES = {
    "vat_turnover": "VAT turnover against revenue",
    "paye": "PAYE gross pay against staff costs",
    "wht": "Withholding tax certificates against supplier payments",
}


def _return(s: Session, tenant_id: str, client_id: str, year_end: date, kind: str) -> ComplianceReturnRow | None:
    return s.scalar(select(ComplianceReturnRow).where(
        ComplianceReturnRow.tenant_id == tenant_id, ComplianceReturnRow.client_id == client_id,
        ComplianceReturnRow.year_end == year_end, ComplianceReturnRow.kind == kind))


def upload(s: Session, p: Principal, client_id: str, year_end: date, kind: str, filename: str, data: bytes):
    require(p, *ANY_ROLE)
    if kind not in READERS:
        raise Invalid(f"kind must be one of {', '.join(READERS)}")
    get_trial_balance(s, p.tenant_id, client_id, year_end)
    try:
        READERS[kind](data)
    except ValueError as exc:
        raise Invalid(str(exc)) from exc
    row = _return(s, p.tenant_id, client_id, year_end, kind)
    if row is None:
        row = ComplianceReturnRow(tenant_id=p.tenant_id, client_id=client_id, year_end=year_end, kind=kind)
        s.add(row)
    row.filename, row.sha256 = filename[:255] or f"{kind}.csv", hashlib.sha256(data).hexdigest()
    row.content, row.uploaded_by = data.decode("utf-8-sig"), p.user
    s.commit()
    return row


def explain(s: Session, p: Principal, client_id: str, year_end: date, check_key: str, text: str) -> None:
    require(p, *ANY_ROLE)
    get_client(s, p.tenant_id, client_id)
    if check_key not in CHECK_FOR.values():
        raise Invalid("unknown check")
    row = s.scalar(select(VarianceExplanationRow).where(
        VarianceExplanationRow.tenant_id == p.tenant_id, VarianceExplanationRow.client_id == client_id,
        VarianceExplanationRow.year_end == year_end, VarianceExplanationRow.check_key == check_key))
    text = text.strip()
    if not text:
        if row is not None:
            s.delete(row)
        s.commit()
        return
    if len(text) < 10:
        raise Invalid("explain the variance in a sentence (e.g. what makes up the difference)")
    if row is None:
        row = VarianceExplanationRow(tenant_id=p.tenant_id, client_id=client_id, year_end=year_end, check_key=check_key)
        s.add(row)
    row.text, row.explained_by = text[:4000], p.user
    s.commit()


def checks(s: Session, tenant_id: str, client_id: str, year_end: date) -> list[dict]:
    """Each check with its upload, its result (None until the return is uploaded) and any explanation."""
    get_client(s, tenant_id, client_id)
    accounts = mapped_accounts(s, tenant_id, client_id, year_end)
    lead = {key: (amount, codes) for key, amount, codes in lead_schedule(accounts)}
    unconfirmed = sum(1 for a in accounts if a.mapping.status != "confirmed" and a.line.balance_cents)
    start, months = year_start(year_end), variance.year_months(year_start(year_end), year_end)
    explanations = {e.check_key: e for e in s.scalars(select(VarianceExplanationRow).where(
        VarianceExplanationRow.tenant_id == tenant_id, VarianceExplanationRow.client_id == client_id,
        VarianceExplanationRow.year_end == year_end))}
    out = []
    for kind, key in CHECK_FOR.items():
        upload_row = _return(s, tenant_id, client_id, year_end, kind)
        result, sources, note = None, [], None
        if upload_row is not None:
            parsed = READERS[kind](upload_row.content.encode())
            if kind == "vat":
                book, sources = lead.get("revenue", (Decimal(0), []))
                result = variance.vat_turnover(book, parsed, months)
            elif kind == "paye":
                book, sources = lead.get("employee_costs", (Decimal(0), []))
                result = variance.paye(book, parsed, months)
            else:
                ledger = s.scalar(select(PurchaseLedgerRow).where(
                    PurchaseLedgerRow.tenant_id == tenant_id, PurchaseLedgerRow.client_id == client_id,
                    PurchaseLedgerRow.year_end == year_end))
                if ledger is None:
                    note = "upload the purchase ledger on the eTIMS tab: payments come from it"
                else:
                    lines = s.scalars(select(PurchaseLineRow).where(PurchaseLineRow.tenant_id == tenant_id,
                                                                    PurchaseLineRow.ledger_id == ledger.id))
                    payments = [(x.supplier_pin, x.supplier, Decimal(x.amount_cents) * CENT) for x in lines]
                    result = variance.wht_on_payments(payments, parsed, start, year_end)
        if unconfirmed and kind != "wht":
            note = f"{unconfirmed} accounts are not yet confirmed in the mapping, so the book figure may change"
        out.append({"key": key, "kind": kind, "upload": upload_row, "result": result, "sources": sources,
                    "note": note, "explanation": explanations.get(key)})
    return out


# --- HTTP -------------------------------------------------------------------------------------------------------


class RowOut(BaseModel):
    label: str
    book: Decimal
    declared: Decimal
    variance: Decimal


class CheckOut(BaseModel):
    key: str
    kind: str  # the upload this check needs: vat | paye | wht
    filename: str | None
    uploaded_by: str | None
    title: str | None
    book_label: str | None
    declared_label: str | None
    book: Decimal | None
    declared: Decimal | None
    variance: Decimal | None
    tolerance: Decimal | None
    needs_explanation: bool
    rows: list[RowOut]
    notes: list[str]
    book_accounts: list[str]  # account codes behind the book figure
    explanation: str | None
    explained_by: str | None
    explained_at: datetime | None


class ExplainIn(BaseModel):
    text: str = Field(max_length=4000)


def _out(c: dict) -> CheckOut:
    r: variance.Check | None = c["result"]
    e, u = c["explanation"], c["upload"]
    return CheckOut(
        key=c["key"], kind=c["kind"], filename=u.filename if u else None, uploaded_by=u.uploaded_by if u else None,
        title=r.title if r else None, book_label=r.book_label if r else None,
        declared_label=r.declared_label if r else None, book=r.book if r else None,
        declared=r.declared if r else None, variance=r.variance if r else None,
        tolerance=r.tolerance if r else None, needs_explanation=bool(r and r.needs_explanation and not e),
        rows=[RowOut(label=x.label, book=x.book, declared=x.declared, variance=x.variance) for x in (r.rows if r else ())],
        notes=[*(r.notes if r else ()), *([c["note"]] if c["note"] else [])], book_accounts=list(c["sources"]),
        explanation=e.text if e else None, explained_by=e.explained_by if e else None,
        explained_at=e.explained_at if e else None,
    )


def add_routes(app: FastAPI, Auth, Db) -> None:  # noqa: N803 - the app's Annotated dependency types
    base = "/clients/{client_id}/years/{year_end}/checks"

    @app.get(base, response_model=list[CheckOut])
    def all_checks(client_id: str, year_end: date, p: Auth, s: Db) -> list[CheckOut]:
        return [_out(c) for c in checks(s, p.tenant_id, client_id, year_end)]

    @app.post(base + "/returns/{kind}", response_model=list[CheckOut], status_code=201)
    def upload_return(client_id: str, year_end: date, kind: str, p: Auth, s: Db,
                      file: UploadFile = File()) -> list[CheckOut]:
        upload(s, p, client_id, year_end, kind, file.filename or "", file.file.read(variance.MAX_BYTES + 1))
        return [_out(c) for c in checks(s, p.tenant_id, client_id, year_end)]

    @app.put(base + "/{check_key}/explanation", response_model=list[CheckOut])
    def explanation(client_id: str, year_end: date, check_key: str, body: ExplainIn, p: Auth, s: Db) -> list[CheckOut]:
        explain(s, p, client_id, year_end, check_key, body.text)
        return [_out(c) for c in checks(s, p.tenant_id, client_id, year_end)]
