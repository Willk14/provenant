"""HTTP routes for clients, trial balances, financial mapping and tax computations."""
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from fastapi import FastAPI, File, Form, UploadFile
from pydantic import BaseModel, Field

from provenant.api import tax
from provenant.core import tb_csv
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


class InputsIn(BaseModel):
    losses_brought_forward: str = "0"
    allowances: list[AllowanceIn] = []


class InputsOut(InputsIn):
    updated_by: str | None = None


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
    allowance: Decimal
    wdv_carried_forward: Decimal
    basis: str


class ComputationOut(BaseModel):
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
    warnings: list[str]
    leases: list[LeaseYearOut]
    allowances: list[AllowanceOut]
    accounts: dict[str, str]  # trial balance line id -> "code name", to label each line's sources


def _tb_out(tb) -> TrialBalanceOut:
    return TrialBalanceOut(id=tb.id, year_end=tb.year_end, filename=tb.filename, sha256=tb.sha256,
                           uploaded_by=tb.uploaded_by)


def _line(c) -> CompLineOut:
    return CompLineOut(label=c.label, amount=c.amount, basis=c.basis, sources=list(c.sources))


def add_routes(app: FastAPI, Auth, Db) -> None:  # noqa: N803 - the app's Annotated dependency types
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
        row = tax.get_inputs(s, p.tenant_id, client_id, year_end)
        if row is None:
            return InputsOut()
        import json

        return InputsOut(losses_brought_forward=str(Decimal(row.losses_brought_forward_cents) / 100),
                         allowances=json.loads(row.allowances_json), updated_by=row.updated_by)

    @app.put("/clients/{client_id}/years/{year_end}/inputs", response_model=InputsOut)
    def set_inputs(client_id: str, year_end: date, body: InputsIn, p: Auth, s: Db) -> InputsOut:
        row = tax.set_inputs(s, p, client_id, year_end, body.losses_brought_forward,
                             [a.model_dump() for a in body.allowances])
        return InputsOut(**body.model_dump(), updated_by=row.updated_by)

    @app.get("/clients/{client_id}/years/{year_end}/tax", response_model=ComputationOut)
    def computation(client_id: str, year_end: date, p: Auth, s: Db) -> ComputationOut:
        r = tax.tax_computation(s, p.tenant_id, client_id, year_end)
        c = r.computation
        return ComputationOut(
            rules_version=c.rules_version, profit_before_tax=c.profit_before_tax,
            adjustments=[_line(a) for a in c.adjustments], taxable_before_losses=c.taxable_before_losses,
            losses_brought_forward=c.losses_brought_forward, losses_used=c.losses_used,
            losses_carried_forward=c.losses_carried_forward, taxable_profit=c.taxable_profit, rate=c.rate,
            tax_payable=c.tax_payable, deferred=[_line(d) for d in c.deferred], deferred_tax=c.deferred_tax,
            warnings=list(c.warnings),
            leases=[LeaseYearOut(**vars(l)) for l in r.leases],
            allowances=[AllowanceOut(name=a.name, allowance=a.allowance(), wdv_carried_forward=a.wdv_carried_forward(),
                                     basis=a.basis) for a in r.allowances],
            accounts={str(a.line.id): f"{a.line.code} {a.line.name}" for a in r.accounts},
        )
