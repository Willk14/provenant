"""HTTP routes for clients, trial balances, financial mapping, tax records and tax computations."""
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, File, Form, Response, UploadFile
from pydantic import BaseModel, Field

from provenant.api import tax
from provenant.api import export as workbook_export
from provenant.api.services import MAX_UPLOAD_BYTES
from provenant.api.storage import BlobStore
from provenant.core.money import to_decimal
from provenant.core import tb_csv
from provenant.tax import register as fa_register
from provenant.tax.engine import CREDIT_KINDS, KENYA
from provenant.tax.instalments import plan_instalments
from provenant.tax.mapping import FS_LINES, TaxTreatment


class ClientIn(BaseModel):
    name: str = Field(max_length=200)


class ClientOut(BaseModel):
    id: str
    name: str
    created_at: datetime
    year_ends: list[date] = []


class TrialBalanceOut(BaseModel):
    id: str
    year_end: date
    filename: str
    sha256: str
    uploaded_by: str


class AccountOut(BaseModel):
    line_id: str
    code: str
    name: str
    balance: Decimal
    fs_line: str | None
    tax_treatment: str | None
    status: str  # unmapped | suggested | confirmed
    rule: str | None
    confirmed_by: str | None


class LeadLineOut(BaseModel):
    fs_line: str
    label: str
    statement: str
    amount: Decimal
    codes: list[str]


class MappingOut(BaseModel):
    trial_balance: TrialBalanceOut
    accounts: list[AccountOut]
    lead_schedule: list[LeadLineOut]
    fs_lines: dict[str, dict[str, Any]]  # the vocabulary, so the UI offers the same choices the server accepts
    tax_treatments: list[str]


class MapIn(BaseModel):
    fs_line: str = Field(max_length=64)
    tax_treatment: str = Field(max_length=32)


class AllowanceIn(BaseModel):
    name: str = Field(max_length=100)
    wdv_brought_forward: str = "0"
    additions: str = "0"
    rate_percent: str
    method: str = "reducing"
    basis: str = Field(max_length=300)
    cost_basis: str = "0"
    disposals: str = "0"
    asset_opening_values: dict[str, str] = Field(default_factory=dict, max_length=20_000)
    business_ceased: bool = False


class CreditIn(BaseModel):
    kind: str = Field(max_length=32)
    description: str = Field(max_length=200)
    amount: str
    reference: str = Field(default="", max_length=100)
    evidence: str | None = Field(default=None, max_length=36)  # supporting document id
    paid_on: str | None = None


class PriorReturnIn(BaseModel):
    reference: str = Field(default="", max_length=100)  # e.g. the KRA acknowledgement number
    filed_on: str = ""
    assessed_tax: str = ""
    agriculture_income_ratio: str = "0"


class InputsIn(BaseModel):
    losses_brought_forward: str = "0"
    allowances: list[AllowanceIn] = Field(default=[], max_length=50)
    credits: list[CreditIn] = Field(default=[], max_length=200)
    prior_return: PriorReturnIn = PriorReturnIn()


class InputsOut(InputsIn):
    updated_by: str | None = None


class AssetOut(BaseModel):
    code: str
    description: str
    asset_class: str
    acquired: date
    cost: Decimal
    disposed: date | None
    proceeds: Decimal


class ClassYearOut(BaseModel):
    asset_class: str
    additions: Decimal
    disposals: Decimal
    cost_held: Decimal
    assets_added: list[str]
    assets_disposed: list[str]
    assets_held: int


class RegisterOut(BaseModel):
    id: str
    filename: str
    sha256: str
    uploaded_by: str
    classes: list[ClassYearOut]
    assets: list[AssetOut]


class AttachmentOut(BaseModel):
    id: str
    year_end: date
    category: str
    filename: str
    content_type: str
    size_bytes: int
    account_code: str | None
    note: str
    uploaded_by: str
    created_at: datetime


class RecordsMetaOut(BaseModel):
    credit_kinds: dict[str, str]
    attachment_categories: dict[str, str]


class CompLineOut(BaseModel):
    label: str
    amount: Decimal
    basis: str
    sources: list[str]


class LeaseYearOut(BaseModel):
    document_id: str
    name: str
    payments: Decimal
    rou_depreciation: Decimal
    interest: Decimal
    rou_carrying_amount: Decimal
    liability: Decimal


class AllowanceOut(BaseModel):
    name: str
    wdv_brought_forward: Decimal
    additions: Decimal
    disposals: Decimal
    rate: Decimal
    method: str
    cost_basis: Decimal
    allowance: Decimal
    disposal_gain: Decimal
    balancing_charge: Decimal
    balancing_deduction: Decimal
    business_ceased: bool
    wdv_carried_forward: Decimal
    basis: str
    from_register: bool
    asset_schedule: list[dict[str, Any]] = Field(default_factory=list)


class CreditItemOut(BaseModel):
    kind: str
    description: str
    amount: Decimal
    reference: str
    evidence: str | None
    evidence_filename: str | None


class ComputationOut(BaseModel):
    approval: dict[str, Any] | None = None
    changed_since_approval: bool = False
    rules_version: str
    profit_before_tax: Decimal
    adjustments: list[CompLineOut]
    taxable_before_losses: Decimal
    losses_brought_forward: Decimal
    losses_used: Decimal
    losses_carried_forward: Decimal
    taxable_profit: Decimal
    rate: Decimal
    tax_payable: Decimal
    deferred: list[CompLineOut]
    deferred_tax: Decimal
    credits: list[CompLineOut]  # sources are "credit:<index>" into credit_items
    tax_due: Decimal  # negative: refundable
    instalment_plan: dict[str, Any] | None = None
    credit_items: list[CreditItemOut]
    warnings: list[str]
    leases: list[LeaseYearOut]
    allowances: list[AllowanceOut]
    accounts: dict[str, str]  # trial balance line id -> "code name", to label each line's sources


def _inputs_out(row) -> InputsOut:
    if row is None:
        return InputsOut()
    return InputsOut(
        losses_brought_forward=str(Decimal(row.losses_brought_forward_cents) / 100),
        allowances=json.loads(row.allowances_json), credits=json.loads(row.credits_json),
        prior_return=json.loads(row.prior_return_json or "{}"), updated_by=row.updated_by,
    )


def _attachment_out(a) -> AttachmentOut:
    return AttachmentOut(id=a.id, year_end=a.year_end, category=a.category, filename=a.filename,
                         content_type=a.content_type, size_bytes=a.size_bytes, account_code=a.account_code,
                         note=a.note, uploaded_by=a.uploaded_by, created_at=a.created_at)


def _tb_out(tb) -> TrialBalanceOut:
    return TrialBalanceOut(id=tb.id, year_end=tb.year_end, filename=tb.filename, sha256=tb.sha256,
                           uploaded_by=tb.uploaded_by)


def _line(c) -> CompLineOut:
    return CompLineOut(label=c.label, amount=c.amount, basis=c.basis, sources=list(c.sources))


def add_routes(app: FastAPI, Auth, Db, store: BlobStore) -> None:  # noqa: N803 - the app's Annotated dependency types
    @app.post("/clients", response_model=ClientOut, status_code=201)
    def create_client(body: ClientIn, p: Auth, s: Db) -> ClientOut:
        c = tax.create_client(s, p, body.name)
        return ClientOut(id=c.id, name=c.name, created_at=c.created_at)

    @app.get("/clients", response_model=list[ClientOut])
    def clients(p: Auth, s: Db) -> list[ClientOut]:
        return [ClientOut(id=c.id, name=c.name, created_at=c.created_at, year_ends=years)
                for c, years in tax.list_clients(s, p.tenant_id)]

    @app.put("/documents/{document_id}/client", status_code=204)
    def link_lease(document_id: str, body: dict, p: Auth, s: Db) -> None:
        tax.link_lease(s, p, document_id, body.get("client_id") or None)

    @app.post("/clients/{client_id}/trial-balances", response_model=TrialBalanceOut, status_code=201)
    def upload_tb(
        client_id: str, p: Auth, s: Db, file: UploadFile = File(), year_end: date = Form()
    ) -> TrialBalanceOut:
        data = file.file.read(tb_csv.MAX_BYTES + 1)
        return _tb_out(tax.upload_trial_balance(s, p, client_id, year_end, file.filename or "", data))

    @app.get("/clients/{client_id}/years/{year_end}/mapping", response_model=MappingOut)
    def mapping(client_id: str, year_end: date, p: Auth, s: Db) -> MappingOut:
        tax.get_client(s, p.tenant_id, client_id)
        accounts = tax.mapped_accounts(s, p.tenant_id, client_id, year_end)
        return MappingOut(
            trial_balance=_tb_out(tax.get_trial_balance(s, p.tenant_id, client_id, year_end)),
            accounts=[
                AccountOut(line_id=str(a.line.id), code=a.line.code, name=a.line.name, balance=a.balance,
                           fs_line=a.mapping.fs_line, tax_treatment=a.mapping.tax_treatment, status=a.mapping.status,
                           rule=a.mapping.rule, confirmed_by=a.mapping.confirmed_by)
                for a in accounts
            ],
            lead_schedule=[
                LeadLineOut(fs_line=key, label=FS_LINES[key].label, statement=FS_LINES[key].statement.value,
                            amount=amount, codes=codes)
                for key, amount, codes in tax.lead_schedule(accounts)
            ],
            fs_lines={k: {"label": v.label, "statement": v.statement.value, "default": v.default_treatment.value}
                      for k, v in FS_LINES.items()},
            tax_treatments=[t.value for t in TaxTreatment],
        )

    @app.put("/clients/{client_id}/mappings/{code}", response_model=AccountOut)
    def map_account(client_id: str, code: str, body: MapIn, p: Auth, s: Db) -> AccountOut:
        m = tax.map_account(s, p, client_id, code, body.fs_line, body.tax_treatment)
        return AccountOut(line_id="", code=m.code, name="", balance=Decimal(0), fs_line=m.fs_line,
                          tax_treatment=m.tax_treatment, status=m.status, rule=m.rule, confirmed_by=m.confirmed_by)

    @app.post("/clients/{client_id}/years/{year_end}/confirm-suggestions")
    def confirm(client_id: str, year_end: date, p: Auth, s: Db) -> dict[str, int]:
        return {"confirmed": tax.confirm_suggestions(s, p, client_id, year_end)}

    @app.get("/clients/{client_id}/years/{year_end}/inputs", response_model=InputsOut)
    def get_inputs(client_id: str, year_end: date, p: Auth, s: Db) -> InputsOut:
        tax.get_client(s, p.tenant_id, client_id)
        return _inputs_out(tax.get_inputs(s, p.tenant_id, client_id, year_end))

    @app.put("/clients/{client_id}/years/{year_end}/inputs", response_model=InputsOut)
    def set_inputs(client_id: str, year_end: date, body: InputsIn, p: Auth, s: Db) -> InputsOut:
        row = tax.set_inputs(s, p, client_id, year_end, body.losses_brought_forward,
                             [a.model_dump() for a in body.allowances], [c.model_dump() for c in body.credits],
                             body.prior_return.model_dump())
        return _inputs_out(row)

    @app.post("/clients/{client_id}/years/{year_end}/inputs/roll-forward", response_model=InputsIn)
    def roll_forward(client_id: str, year_end: date, p: Auth, s: Db) -> InputsIn:
        return InputsIn(**tax.roll_forward(s, p, client_id, year_end))

    @app.get("/tax-records/meta", response_model=RecordsMetaOut)
    def records_meta(p: Auth) -> RecordsMetaOut:
        return RecordsMetaOut(credit_kinds=CREDIT_KINDS, attachment_categories=tax.ATTACHMENT_CATEGORIES)

    @app.post("/clients/{client_id}/years/{year_end}/fixed-assets", response_model=RegisterOut, status_code=201)
    def upload_register(client_id: str, year_end: date, p: Auth, s: Db, file: UploadFile = File()) -> RegisterOut:
        data = file.file.read(fa_register.MAX_BYTES + 1)
        reg = tax.upload_register(s, p, client_id, year_end, file.filename or "", data)
        return _register_out(s, p.tenant_id, reg)

    @app.get("/clients/{client_id}/years/{year_end}/fixed-assets", response_model=RegisterOut | None)
    def register(client_id: str, year_end: date, p: Auth, s: Db) -> RegisterOut | None:
        tax.get_client(s, p.tenant_id, client_id)
        reg = tax.get_register(s, p.tenant_id, client_id, year_end)
        return _register_out(s, p.tenant_id, reg) if reg else None

    @app.post("/clients/{client_id}/years/{year_end}/attachments", response_model=AttachmentOut, status_code=201)
    def upload_attachment(
        client_id: str, year_end: date, p: Auth, s: Db, file: UploadFile = File(), category: str = Form(),
        account_code: str = Form(""), note: str = Form(""),
    ) -> AttachmentOut:
        data = file.file.read(MAX_UPLOAD_BYTES + 1)
        return _attachment_out(tax.upload_attachment(s, p, store, client_id, year_end, category,
                                                     file.filename or "", data, account_code, note))

    @app.get("/clients/{client_id}/years/{year_end}/attachments", response_model=list[AttachmentOut])
    def attachments(client_id: str, year_end: date, p: Auth, s: Db) -> list[AttachmentOut]:
        tax.get_client(s, p.tenant_id, client_id)
        return [_attachment_out(a) for a in tax.list_attachments(s, p.tenant_id, client_id, year_end)]

    @app.get("/clients/{client_id}/attachments/{attachment_id}/file")
    def attachment_file(client_id: str, attachment_id: str, p: Auth, s: Db) -> Response:
        a = tax.get_attachment(s, p.tenant_id, client_id, attachment_id)
        inline = a.content_type in ("application/pdf", "image/png", "image/jpeg")
        return Response(store.get(a.sha256), media_type=a.content_type, headers={
            "Content-Disposition": f"{'inline' if inline else 'attachment'}; filename*=UTF-8''{quote(a.filename)}",
            "X-Content-Type-Options": "nosniff",
        })

    @app.delete("/clients/{client_id}/attachments/{attachment_id}", status_code=204)
    def delete_attachment(client_id: str, attachment_id: str, p: Auth, s: Db) -> None:
        tax.delete_attachment(s, p, client_id, attachment_id)

    @app.get("/clients/{client_id}/years/{year_end}/tax", response_model=ComputationOut)
    def computation(client_id: str, year_end: date, p: Auth, s: Db) -> ComputationOut:
        r = tax.tax_computation(s, p.tenant_id, client_id, year_end)
        latest, is_current = tax.current_tax_approval(s, p.tenant_id, client_id, year_end)
        c = r.computation
        inputs_row = tax.get_inputs(s, p.tenant_id, client_id, year_end)
        planning = json.loads(inputs_row.prior_return_json or "{}") if inputs_row else {}
        assessed = planning.get("assessed_tax") or ""
        ratio = Decimal(planning.get("agriculture_income_ratio") or "0")
        raw_credits = json.loads(inputs_row.credits_json) if inputs_row else []
        withholding = sum((x.amount for x in r.credits if x.kind == "withholding"), Decimal(0))
        paid = tuple((date.fromisoformat(x["paid_on"]), to_decimal(str(x["amount"]))) for x in raw_credits
                     if x.get("kind") == "instalment" and x.get("paid_on"))
        plan = plan_instalments(year_end, c.tax_payable,
                                Decimal(assessed) if assessed else None,
                                withholding, ratio, paid, c.tax_due)
        return ComputationOut(
            approval=({"reviewer": latest.reviewer, "approved_at": latest.approved_at.isoformat()}
                      if latest else None),
            changed_since_approval=bool(latest and not is_current),
            rules_version=c.rules_version, profit_before_tax=c.profit_before_tax,
            adjustments=[_line(a) for a in c.adjustments], taxable_before_losses=c.taxable_before_losses,
            losses_brought_forward=c.losses_brought_forward, losses_used=c.losses_used,
            losses_carried_forward=c.losses_carried_forward, taxable_profit=c.taxable_profit, rate=c.rate,
            tax_payable=c.tax_payable, deferred=[_line(d) for d in c.deferred], deferred_tax=c.deferred_tax,
            credits=[_line(x) for x in c.credits], tax_due=c.tax_due,
            instalment_plan={
                "annual_amount": plan.annual_amount, "basis_used": plan.basis_used,
                "prior_year_basis": plan.prior_year_basis, "current_year_basis": plan.current_year_basis,
                "withholding_reduction": plan.withholding_reduction,
                "agriculture_income_ratio": plan.agriculture_income_ratio,
                "balance_due_date": plan.balance_due_date,
                "balance_due_basis": plan.balance_due_basis,
                "balance_due_amount": plan.balance_due_amount,
                "unallocated_instalment_payments": sum((to_decimal(str(x["amount"])) for x in raw_credits
                    if x.get("kind") == "instalment" and not x.get("paid_on")), Decimal(0)),
                "installments":[{"number": x.number, "due_date": x.due_date, "proportion": x.proportion,
                                  "amount": x.amount, "paid_to_date": x.paid_to_date,
                                  "outstanding": x.outstanding, "basis": x.basis, "sources": list(x.sources)}
                                 for x in plan.installments],
            },
            credit_items=[
                CreditItemOut(kind=x.kind, description=x.description, amount=x.amount, reference=x.reference,
                              evidence=x.evidence, evidence_filename=r.attachments.get(x.evidence or ""))
                for x in r.credits
            ],
            warnings=list(c.warnings),
            leases=[LeaseYearOut(**vars(l)) for l in r.leases],
            allowances=[AllowanceOut(name=a.name, wdv_brought_forward=a.wdv_brought_forward,
                                     additions=a.additions, disposals=a.disposals, rate=a.rate, method=a.method,
                                     cost_basis=a.cost_basis,
                                     allowance=a.allowance(), disposal_gain=a.disposal_gain(),
                                     balancing_charge=a.balancing_charge(),
                                     balancing_deduction=a.cessation_deduction(), business_ceased=a.business_ceased,
                                     wdv_carried_forward=a.wdv_carried_forward(), basis=a.basis,
                                     from_register=a.name in r.from_register,
                                     asset_schedule=[{
                                         "code": item.code, "opening_wdv": item.opening_wdv,
                                         "additions": item.additions, "allowance": item.allowance,
                                         "proceeds": item.proceeds, "disposal_result": item.disposal_result,
                                         "closing_wdv": item.closing_wdv, "rate_basis": a.basis,
                                         "disposal_basis": (KENYA.basis["cessation"] if a.business_ceased and item.disposal_result
                                                            else KENYA.basis["disposal_gain"] if item.disposal_result and item.disposal_result > 0
                                                            else KENYA.basis["disposal_loss"] if item.disposal_result and item.disposal_result < 0
                                                            else ""),
                                         "source": f"asset:{item.code}",
                                     } for item in a.asset_schedule()]) for a in r.allowances],
            accounts={str(a.line.id): f"{a.line.code} {a.line.name}" for a in r.accounts},
        )

    @app.post("/clients/{client_id}/years/{year_end}/tax/approval")
    def approve_computation(client_id: str, year_end: date, p: Auth, s: Db) -> dict[str, Any]:
        row = tax.approve_tax(s, p, client_id, year_end)
        return {"reviewer": row.reviewer, "approved_at": row.approved_at.isoformat(),
                "fingerprint": row.fingerprint}

    @app.get("/clients/{client_id}/years/{year_end}/tax.xlsx")
    def tax_export(client_id: str, year_end: date, p: Auth, s: Db) -> Response:
        result = computation(client_id, year_end, p, s)
        accounts = tax.mapped_accounts(s, p.tenant_id, client_id, year_end)
        profit_sources = [str(a.line.id) for a in accounts
                          if FS_LINES[a.mapping.fs_line].statement.value == "profit_or_loss"
                          and a.mapping.tax_treatment != "tax_charge"]
        content = workbook_export.tax_workbook(result.model_dump(mode="python"), tax.lead_schedule(accounts),
                                                profit_sources)
        filename = f"tax-computation-{year_end.isoformat()}.xlsx"
        return Response(content=content,
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _register_out(s, tenant_id: str, reg) -> RegisterOut:
    assets = tax.register_assets(s, tenant_id, reg)
    return RegisterOut(
        id=reg.id, filename=reg.filename, sha256=reg.sha256, uploaded_by=reg.uploaded_by,
        classes=[ClassYearOut(asset_class=c.asset_class, additions=c.additions, disposals=c.disposals,
                              cost_held=c.cost_held, assets_added=list(c.assets_added),
                              assets_disposed=list(c.assets_disposed), assets_held=c.assets_held)
                 for c in tax.register_summary(s, tenant_id, reg)],
        assets=[AssetOut(code=a.code, description=a.description, asset_class=a.asset_class, acquired=a.acquired,
                         cost=a.cost, disposed=a.disposed, proceeds=a.proceeds) for a in assets],
    )
