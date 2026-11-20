"""eTIMS reconciliation use cases and routes: purchase ledger and eTIMS export per client-year, a person's
decisions per purchase, and applying the unsupported total to the tax computation (ADR 0007)."""
import hashlib
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import FastAPI, File, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from provenant.api.auth import Principal
from provenant.api.db import (
    EtimsDecisionRow,
    EtimsExportRow,
    EtimsInvoiceRow,
    PurchaseLedgerRow,
    PurchaseLineRow,
)
from provenant.api.services import Conflict, Invalid, as_csv, require
from provenant.api.tax import ANY_ROLE, CENT, get_client, get_trial_balance, mapped_accounts
from provenant.tax import etims
from provenant.tax.mapping import TaxTreatment

# Purchases are expenses for the add-back only when posted to an account the firm treats as deductible.
DEDUCTIBLE = {TaxTreatment.DEDUCTIBLE.value}


def _get(s: Session, model, tenant_id: str, client_id: str, year_end: date):
    return s.scalar(select(model).where(model.tenant_id == tenant_id, model.client_id == client_id,
                                        model.year_end == year_end))


def _replace(s: Session, model, line_model, fk: str, p: Principal, client_id: str, year_end: date, filename: str,
             data: bytes):
    if (old := _get(s, model, p.tenant_id, client_id, year_end)) is not None:
        for row in s.scalars(select(line_model).where(getattr(line_model, fk) == old.id)):
            s.delete(row)
        s.delete(old)
        s.flush()
    row = model(tenant_id=p.tenant_id, client_id=client_id, year_end=year_end, filename=filename[:255] or "upload.csv",
                sha256=hashlib.sha256(data).hexdigest(), uploaded_by=p.user)
    s.add(row)
    s.flush()
    return row


def upload_ledger(s: Session, p: Principal, client_id: str, year_end: date, filename: str, data: bytes):
    require(p, *ANY_ROLE)
    get_trial_balance(s, p.tenant_id, client_id, year_end)
    try:
        lines = etims.read_ledger(as_csv(data, filename))
    except ValueError as exc:
        raise Invalid(str(exc)) from exc
    ledger = _replace(s, PurchaseLedgerRow, PurchaseLineRow, "ledger_id", p, client_id, year_end, filename, data)
    s.add_all(PurchaseLineRow(
        tenant_id=p.tenant_id, ledger_id=ledger.id, row_number=x.number, date=x.date, supplier=x.supplier,
        supplier_pin=x.supplier_pin, invoice_number=x.invoice_number, account_code=x.account_code,
        amount_cents=int(x.amount / CENT), etims_number=x.etims_number, description=x.description,
    ) for x in lines)
    s.commit()
    return ledger


def upload_invoices(s: Session, p: Principal, client_id: str, year_end: date, filename: str, data: bytes):
    require(p, *ANY_ROLE)
    get_trial_balance(s, p.tenant_id, client_id, year_end)
    try:
        invoices = etims.read_etims(as_csv(data, filename))
    except ValueError as exc:
        raise Invalid(str(exc)) from exc
    export = _replace(s, EtimsExportRow, EtimsInvoiceRow, "export_id", p, client_id, year_end, filename, data)
    s.add_all(EtimsInvoiceRow(
        tenant_id=p.tenant_id, export_id=export.id, row_number=x.number, cu_invoice_number=x.cu_invoice_number,
        supplier_pin=x.supplier_pin, supplier_name=x.supplier_name, invoice_number=x.invoice_number, date=x.date,
        taxable_cents=int(x.taxable_value / CENT), vat_cents=int(x.vat / CENT),
    ) for x in invoices)
    # A new export changes what is matched: the preparer applies the add-back again.
    if (ledger := _get(s, PurchaseLedgerRow, p.tenant_id, client_id, year_end)) is not None:
        ledger.applied_by, ledger.applied_at = None, None
    s.commit()
    return export


@dataclass(frozen=True)
class Run:
    ledger: PurchaseLedgerRow | None
    export: EtimsExportRow | None
    reconciliation: etims.Reconciliation | None
    line_ids: dict[int, int]  # CSV row number -> purchase_lines.id, for sources
    treatments: dict[str, str]  # account code -> tax treatment (confirmed mappings)

    def counts_for_addback(self, result: etims.LineResult) -> bool:
        return self.treatments.get(result.line.account_code) in DEDUCTIBLE

    @property
    def addback(self) -> list[tuple[str, Decimal]]:
        if self.reconciliation is None:
            return []
        return [(f"etims:{self.line_ids[r.line.number]}", r.unsupported) for r in self.reconciliation.lines
                if r.unsupported and self.counts_for_addback(r)]

    @property
    def probable(self) -> int:
        return sum(1 for r in self.reconciliation.lines if r.status is etims.Status.PROBABLE) if self.reconciliation else 0


def run(s: Session, tenant_id: str, client_id: str, year_end: date) -> Run:
    get_client(s, tenant_id, client_id)
    ledger = _get(s, PurchaseLedgerRow, tenant_id, client_id, year_end)
    export = _get(s, EtimsExportRow, tenant_id, client_id, year_end)
    treatments = {a.line.code: a.mapping.tax_treatment for a in mapped_accounts(s, tenant_id, client_id, year_end)
                  if a.mapping.status == "confirmed"}
    if ledger is None or export is None:
        return Run(ledger, export, None, {}, treatments)
    rows = s.scalars(select(PurchaseLineRow).where(PurchaseLineRow.tenant_id == tenant_id,
                                                   PurchaseLineRow.ledger_id == ledger.id)
                     .order_by(PurchaseLineRow.row_number)).all()
    lines = [etims.PurchaseLine(r.row_number, r.date, r.supplier, r.supplier_pin, r.invoice_number, r.account_code,
                                Decimal(r.amount_cents) * CENT, r.etims_number, r.description) for r in rows]
    invoices = [
        etims.EtimsInvoice(r.row_number, r.cu_invoice_number, r.supplier_pin, r.invoice_number, r.date,
                           Decimal(r.taxable_cents) * CENT, Decimal(r.vat_cents) * CENT, r.supplier_name)
        for r in s.scalars(select(EtimsInvoiceRow).where(EtimsInvoiceRow.tenant_id == tenant_id,
                                                         EtimsInvoiceRow.export_id == export.id)
                           .order_by(EtimsInvoiceRow.row_number))
    ]
    decisions = {
        d.line_key: etims.Decision(d.kind, d.reason, d.cu_invoice_number, d.decided_by)
        for d in s.scalars(select(EtimsDecisionRow).where(
            EtimsDecisionRow.tenant_id == tenant_id, EtimsDecisionRow.client_id == client_id,
            EtimsDecisionRow.year_end == year_end))
    }
    return Run(ledger, export, etims.reconcile(lines, invoices, decisions), {r.row_number: r.id for r in rows},
               treatments)


def decide(s: Session, p: Principal, client_id: str, year_end: date, line_key: str, kind: str, reason: str = "",
           cu_invoice_number: str = "") -> None:
    """Exempt a purchase (with a reason), match it to a named eTIMS invoice, or clear the decision."""
    require(p, *ANY_ROLE)
    get_client(s, p.tenant_id, client_id)
    row = s.scalar(select(EtimsDecisionRow).where(
        EtimsDecisionRow.tenant_id == p.tenant_id, EtimsDecisionRow.client_id == client_id,
        EtimsDecisionRow.year_end == year_end, EtimsDecisionRow.line_key == line_key))
    if kind == "clear":
        if row is not None:
            s.delete(row)
    else:
        try:
            etims.Decision(kind, reason, cu_invoice_number)
        except ValueError as exc:
            raise Invalid(str(exc)) from exc
        if row is None:
            row = EtimsDecisionRow(tenant_id=p.tenant_id, client_id=client_id, year_end=year_end, line_key=line_key)
            s.add(row)
        row.kind, row.reason, row.cu_invoice_number, row.decided_by = (
            kind, reason.strip()[:500], cu_invoice_number.strip()[:64], p.user)
    if (ledger := _get(s, PurchaseLedgerRow, p.tenant_id, client_id, year_end)) is not None:
        ledger.applied_by, ledger.applied_at = None, None  # the total changed: apply it again
    s.commit()


def apply(s: Session, p: Principal, client_id: str, year_end: date, on: bool) -> PurchaseLedgerRow:
    """Put the unsupported total into the tax computation, or take it out. Probable matches must be decided first."""
    require(p, *ANY_ROLE)
    r = run(s, p.tenant_id, client_id, year_end)
    if r.ledger is None or r.reconciliation is None:
        raise Conflict("upload both the purchase ledger and the eTIMS export first")
    if on and r.probable:
        raise Conflict(f"{r.probable} probable matches still need a person to confirm or reject them")
    r.ledger.applied_by, r.ledger.applied_at = (p.user, datetime.now(UTC)) if on else (None, None)
    s.commit()
    return r.ledger


def addback(s: Session, tenant_id: str, client_id: str, year_end: date) -> tuple[list[tuple[str, Decimal]], dict[str, str]]:
    """The applied add-back for the tax computation, and a label for each source."""
    r = run(s, tenant_id, client_id, year_end)
    if r.ledger is None or r.ledger.applied_by is None or r.reconciliation is None:
        return [], {}
    labels = {f"etims:{r.line_ids[x.line.number]}": f"{x.line.supplier} {x.line.invoice_number}".strip()
              for x in r.reconciliation.lines}
    return r.addback, labels


# --- HTTP -------------------------------------------------------------------------------------------------------


class UploadOut(BaseModel):
    filename: str
    sha256: str
    uploaded_by: str


class LineOut(BaseModel):
    key: str
    row_number: int
    date: date
    supplier: str
    supplier_pin: str
    invoice_number: str
    account_code: str
    amount: Decimal
    status: str
    note: str
    decided_by: str
    cu_invoice_number: str | None
    etims_amount: Decimal | None
    unsupported: Decimal
    in_addback: bool  # posted to a deductible account, so it counts toward the add-back


class InvoiceOut(BaseModel):
    cu_invoice_number: str
    supplier_pin: str
    supplier_name: str
    invoice_number: str
    date: date
    taxable_value: Decimal


class EtimsOut(BaseModel):
    ledger: UploadOut | None
    export: UploadOut | None
    lines: list[LineOut]
    etims_only: list[InvoiceOut]
    totals: dict[str, Decimal]  # by status, plus "addback"
    probable: int
    applied_by: str | None
    applied_at: datetime | None


class DecisionIn(BaseModel):
    line_key: str = Field(max_length=200)
    kind: str = Field(pattern="^(exempt|match|clear)$")
    reason: str = Field(default="", max_length=500)
    cu_invoice_number: str = Field(default="", max_length=64)


class ApplyIn(BaseModel):
    apply: bool


def _upload_out(row) -> UploadOut | None:
    return UploadOut(filename=row.filename, sha256=row.sha256, uploaded_by=row.uploaded_by) if row else None


def _out(r: Run) -> EtimsOut:
    rec = r.reconciliation
    lines = [] if rec is None else [
        LineOut(key=x.line.key, row_number=x.line.number, date=x.line.date, supplier=x.line.supplier,
                supplier_pin=x.line.supplier_pin, invoice_number=x.line.invoice_number,
                account_code=x.line.account_code, amount=x.line.amount, status=x.status.value, note=x.note,
                decided_by=x.decided_by, cu_invoice_number=x.invoice.cu_invoice_number if x.invoice else None,
                etims_amount=x.invoice.taxable_value if x.invoice else None, unsupported=x.unsupported,
                in_addback=r.counts_for_addback(x))
        for x in rec.lines
    ]
    totals = {status.value: rec.total(status) for status in etims.Status} if rec else {}
    if rec:
        totals["addback"] = sum((a for _, a in r.addback), Decimal(0))
    return EtimsOut(
        ledger=_upload_out(r.ledger), export=_upload_out(r.export), lines=lines,
        etims_only=[InvoiceOut(cu_invoice_number=i.cu_invoice_number, supplier_pin=i.supplier_pin,
                               supplier_name=i.supplier_name, invoice_number=i.invoice_number, date=i.date,
                               taxable_value=i.taxable_value) for i in (rec.etims_only if rec else ())],
        totals=totals, probable=r.probable,
        applied_by=r.ledger.applied_by if r.ledger else None, applied_at=r.ledger.applied_at if r.ledger else None,
    )


def add_routes(app: FastAPI, Auth, Db) -> None:  # noqa: N803 - the app's Annotated dependency types
    base = "/clients/{client_id}/years/{year_end}/etims"

    @app.get(base, response_model=EtimsOut)
    def reconciliation(client_id: str, year_end: date, p: Auth, s: Db) -> EtimsOut:
        return _out(run(s, p.tenant_id, client_id, year_end))

    @app.post(f"{base}/ledger", response_model=EtimsOut, status_code=201)
    def ledger(client_id: str, year_end: date, p: Auth, s: Db, file: UploadFile = File()) -> EtimsOut:
        upload_ledger(s, p, client_id, year_end, file.filename or "", file.file.read(etims.MAX_BYTES + 1))
        return _out(run(s, p.tenant_id, client_id, year_end))

    @app.post(f"{base}/invoices", response_model=EtimsOut, status_code=201)
    def invoices(client_id: str, year_end: date, p: Auth, s: Db, file: UploadFile = File()) -> EtimsOut:
        upload_invoices(s, p, client_id, year_end, file.filename or "", file.file.read(etims.MAX_BYTES + 1))
        return _out(run(s, p.tenant_id, client_id, year_end))

    @app.put(f"{base}/decisions", response_model=EtimsOut)
    def decision(client_id: str, year_end: date, body: DecisionIn, p: Auth, s: Db) -> EtimsOut:
        decide(s, p, client_id, year_end, body.line_key, body.kind, body.reason, body.cu_invoice_number)
        return _out(run(s, p.tenant_id, client_id, year_end))

    @app.post(f"{base}/apply", response_model=EtimsOut)
    def apply_route(client_id: str, year_end: date, body: ApplyIn, p: Auth, s: Db) -> EtimsOut:
        apply(s, p, client_id, year_end, body.apply)
        return _out(run(s, p.tenant_id, client_id, year_end))
