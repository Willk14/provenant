"""Tax consultancy use cases: clients, trial balances, financial mapping and corporate tax computations."""
import hashlib
import json
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from provenant.api.auth import PARTNER, PREPARER, REVIEWER, Principal
from provenant.api.db import (
    AccountMappingEventRow,
    AccountMappingRow,
    ClientRow,
    ClientTBLineRow,
    ClientTrialBalanceRow,
    Document,
    TaxInputsRow,
)
from provenant.api.services import Conflict, Invalid, NotFound, get_document, lease_schedule, require
from provenant.core import tb_csv
from provenant.core.money import ZERO, to_decimal
from provenant.covenants.engine import month_end
from provenant.tax.engine import KENYA, AllowanceClass, Computation, LeaseYear, MappedLine, compute
from provenant.tax.mapping import FS_LINES, TaxTreatment, check_mapping, suggest

ANY_ROLE = (PREPARER, REVIEWER, PARTNER)
CENT = Decimal("0.01")


def get_client(s: Session, tenant_id: str, client_id: str) -> ClientRow:
    client = s.scalar(select(ClientRow).where(ClientRow.tenant_id == tenant_id, ClientRow.id == client_id))
    if client is None:
        raise NotFound("client not found")
    return client


def create_client(s: Session, p: Principal, name: str) -> ClientRow:
    require(p, *ANY_ROLE)
    name = " ".join(name.split())
    if not 2 <= len(name) <= 200:
        raise Invalid("client name must be 2 to 200 characters")
    client = ClientRow(tenant_id=p.tenant_id, name=name)
    s.add(client)
    try:
        s.commit()
    except IntegrityError as exc:
        s.rollback()
        raise Conflict(f"a client named {name!r} already exists") from exc
    return client


def list_clients(s: Session, tenant_id: str) -> list[tuple[ClientRow, list[date]]]:
    clients = s.scalars(select(ClientRow).where(ClientRow.tenant_id == tenant_id).order_by(ClientRow.name)).all()
    years = s.execute(
        select(ClientTrialBalanceRow.client_id, ClientTrialBalanceRow.year_end)
        .where(ClientTrialBalanceRow.tenant_id == tenant_id)
        .order_by(ClientTrialBalanceRow.year_end.desc())
    ).all()
    by_client: dict[str, list[date]] = {}
    for client_id, year_end in years:
        by_client.setdefault(client_id, []).append(year_end)
    return [(c, by_client.get(c.id, [])) for c in clients]


def link_lease(s: Session, p: Principal, document_id: str, client_id: str | None) -> Document:
    require(p, *ANY_ROLE)
    doc = get_document(s, p.tenant_id, document_id)
    if doc.kind != "lease":
        raise Conflict("only leases are linked to clients (amendments follow their lease)")
    if client_id:
        get_client(s, p.tenant_id, client_id)
    doc.client_id = client_id
    s.commit()
    return doc


def client_leases(s: Session, tenant_id: str, client_id: str) -> list[Document]:
    return list(s.scalars(
        select(Document).where(Document.tenant_id == tenant_id, Document.client_id == client_id, Document.kind == "lease")
    ))


def upload_trial_balance(
    s: Session, p: Principal, client_id: str, year_end: date, filename: str, data: bytes
) -> ClientTrialBalanceRow:
    """Store a year's trial balance and suggest a mapping for every account not mapped before."""
    require(p, *ANY_ROLE)
    client = get_client(s, p.tenant_id, client_id)
    if year_end != month_end(year_end.year, year_end.month):
        raise Invalid("the financial year end must be a month-end")
    try:
        rows = tb_csv.read(data)
    except ValueError as exc:
        raise Invalid(str(exc)) from exc
    tb = ClientTrialBalanceRow(
        tenant_id=p.tenant_id, client_id=client.id, year_end=year_end, filename=filename[:255] or "trial-balance.csv",
        sha256=hashlib.sha256(data).hexdigest(), uploaded_by=p.user,
    )
    s.add(tb)
    try:
        s.flush()
    except IntegrityError as exc:
        s.rollback()
        raise Conflict(f"a trial balance for the year ended {year_end} is already uploaded") from exc
    s.add_all(
        ClientTBLineRow(tenant_id=p.tenant_id, trial_balance_id=tb.id, code=r.code, name=r.name,
                        balance_cents=int(r.balance / CENT))
        for r in rows
    )
    known = {m.code for m in _mappings(s, p.tenant_id, client.id)}
    for r in rows:
        if r.code in known:
            continue  # confirmed mappings carry forward from earlier years
        guess = suggest(r.name)
        s.add(AccountMappingRow(
            tenant_id=p.tenant_id, client_id=client.id, code=r.code, fs_line=guess.fs_line,
            tax_treatment=guess.tax_treatment.value if guess.tax_treatment else None,
            status="suggested" if guess.fs_line else "unmapped", rule=guess.rule,
        ))
    s.commit()
    return tb


def _mappings(s: Session, tenant_id: str, client_id: str) -> list[AccountMappingRow]:
    return list(s.scalars(
        select(AccountMappingRow).where(AccountMappingRow.tenant_id == tenant_id, AccountMappingRow.client_id == client_id)
    ))


def get_trial_balance(s: Session, tenant_id: str, client_id: str, year_end: date) -> ClientTrialBalanceRow:
    tb = s.scalar(select(ClientTrialBalanceRow).where(
        ClientTrialBalanceRow.tenant_id == tenant_id, ClientTrialBalanceRow.client_id == client_id,
        ClientTrialBalanceRow.year_end == year_end,
    ))
    if tb is None:
        raise NotFound(f"no trial balance for the year ended {year_end}")
    return tb


@dataclass(frozen=True)
class MappedAccount:
    line: ClientTBLineRow
    mapping: AccountMappingRow

    @property
    def balance(self) -> Decimal:
        return Decimal(self.line.balance_cents) * CENT


def mapped_accounts(s: Session, tenant_id: str, client_id: str, year_end: date) -> list[MappedAccount]:
    tb = get_trial_balance(s, tenant_id, client_id, year_end)
    lines = s.scalars(select(ClientTBLineRow).where(
        ClientTBLineRow.tenant_id == tenant_id, ClientTBLineRow.trial_balance_id == tb.id
    ).order_by(ClientTBLineRow.code)).all()
    mappings = {m.code: m for m in _mappings(s, tenant_id, client_id)}
    return [MappedAccount(line, mappings[line.code]) for line in lines]


def map_account(s: Session, p: Principal, client_id: str, code: str, fs_line: str, tax_treatment: str) -> AccountMappingRow:
    """A person sets (or confirms) where an account goes. Every decision is kept in the mapping history."""
    require(p, *ANY_ROLE)
    get_client(s, p.tenant_id, client_id)
    try:
        treatment = check_mapping(fs_line, tax_treatment)
    except ValueError as exc:
        raise Invalid(str(exc)) from exc
    row = s.scalar(select(AccountMappingRow).where(
        AccountMappingRow.tenant_id == p.tenant_id, AccountMappingRow.client_id == client_id, AccountMappingRow.code == code
    ))
    if row is None:
        raise NotFound(f"account {code!r} is not in any of this client's trial balances")
    row.fs_line, row.tax_treatment, row.status, row.confirmed_by = fs_line, treatment.value, "confirmed", p.user
    s.add(AccountMappingEventRow(tenant_id=p.tenant_id, client_id=client_id, code=code, fs_line=fs_line,
                                 tax_treatment=treatment.value, by=p.user))
    s.commit()
    return row


def confirm_suggestions(s: Session, p: Principal, client_id: str, year_end: date) -> int:
    """Confirm every suggested mapping in a year's trial balance as is. Unmapped accounts stay for a person."""
    require(p, *ANY_ROLE)
    count = 0
    for account in mapped_accounts(s, p.tenant_id, client_id, year_end):
        m = account.mapping
        if m.status == "suggested":
            m.status, m.confirmed_by = "confirmed", p.user
            s.add(AccountMappingEventRow(tenant_id=p.tenant_id, client_id=client_id, code=m.code, fs_line=m.fs_line,
                                         tax_treatment=m.tax_treatment, by=p.user))
            count += 1
    s.commit()
    return count


def parse_allowances(raw: list[dict]) -> list[AllowanceClass]:
    try:
        return [
            AllowanceClass(
                name=str(a["name"]).strip()[:100],
                wdv_brought_forward=to_decimal(str(a.get("wdv_brought_forward", "0"))),
                additions=to_decimal(str(a.get("additions", "0"))),
                rate=to_decimal(str(a["rate_percent"])) / 100,
                method=str(a.get("method", "reducing")),
                basis=str(a.get("basis", "")),
                cost_basis=to_decimal(str(a.get("cost_basis", "0"))),
            )
            for a in raw
        ]
    except (KeyError, TypeError, ValueError) as exc:
        raise Invalid(f"capital deduction class: {exc}") from exc


def set_inputs(
    s: Session, p: Principal, client_id: str, year_end: date, losses_brought_forward: str, allowances: list[dict]
) -> TaxInputsRow:
    require(p, *ANY_ROLE)
    get_trial_balance(s, p.tenant_id, client_id, year_end)
    parse_allowances(allowances)  # validate before storing
    try:
        losses = to_decimal(losses_brought_forward or "0")
    except (ValueError, TypeError) as exc:
        raise Invalid(f"losses brought forward: {exc}") from exc
    if losses < 0:
        raise Invalid("losses brought forward cannot be negative")
    row = s.scalar(select(TaxInputsRow).where(
        TaxInputsRow.tenant_id == p.tenant_id, TaxInputsRow.client_id == client_id, TaxInputsRow.year_end == year_end
    ))
    if row is None:
        row = TaxInputsRow(tenant_id=p.tenant_id, client_id=client_id, year_end=year_end, updated_by=p.user)
        s.add(row)
    row.losses_brought_forward_cents = int(losses / CENT)
    row.allowances_json = json.dumps(allowances)
    row.updated_by = p.user
    s.commit()
    return row


def get_inputs(s: Session, tenant_id: str, client_id: str, year_end: date) -> TaxInputsRow | None:
    return s.scalar(select(TaxInputsRow).where(
        TaxInputsRow.tenant_id == tenant_id, TaxInputsRow.client_id == client_id, TaxInputsRow.year_end == year_end
    ))


def lease_year(s: Session, tenant_id: str, lease: Document, year_end: date) -> LeaseYear | None:
    """A lease's IFRS 16 figures for the twelve months to `year_end`, from its verified schedule."""
    try:
        chained = lease_schedule(s, tenant_id, lease.id)
    except Conflict as exc:
        raise Conflict(f"lease {lease.filename}: {exc}") from exc
    sched = chained.schedule
    # A period runs from one boundary up to (not including) its end date, so it belongs to the year that
    # holds its last day. A lease starting on the 1st has a December period ending on 1 January.
    previous_year_end = month_end(year_end.year - 1, year_end.month)
    last_day = lambda p: p.end_date - timedelta(days=1)  # noqa: E731
    in_year = [p for p in sched.periods if previous_year_end < last_day(p) <= year_end]
    to_date = [p for p in sched.periods if last_day(p) <= year_end]
    if not to_date:
        return None  # not yet commenced at the year end
    remeasured = [r for r in sched.remeasurements if r.effective <= year_end]
    rou = sched.initial_rou_asset + sum((r.rou_adjustment for r in remeasured), ZERO) - sum(
        (p.depreciation for p in to_date), ZERO
    )
    last = to_date[-1]
    liability = next((r.liability_after for r in remeasured if r.after_period == last.number), last.closing)
    return LeaseYear(
        document_id=lease.id, name=lease.filename,
        payments=sum((p.payment for p in in_year), ZERO),
        rou_depreciation=sum((p.depreciation for p in in_year), ZERO),
        interest=sum((p.interest for p in in_year), ZERO),
        rou_carrying_amount=rou, liability=liability,
    )


@dataclass(frozen=True)
class TaxRun:
    computation: Computation
    accounts: list[MappedAccount]
    leases: list[LeaseYear]
    allowances: list[AllowanceClass]


def tax_computation(s: Session, tenant_id: str, client_id: str, year_end: date) -> TaxRun:
    get_client(s, tenant_id, client_id)
    accounts = mapped_accounts(s, tenant_id, client_id, year_end)
    unconfirmed = [a.mapping.code for a in accounts if a.mapping.status != "confirmed" and a.line.balance_cents]
    if unconfirmed:
        preview = ", ".join(unconfirmed[:8]) + ("…" if len(unconfirmed) > 8 else "")
        raise Conflict(f"{len(unconfirmed)} accounts still need a confirmed mapping: {preview}")
    lines = [
        MappedLine(str(a.line.id), a.line.code, a.line.name, a.balance, a.mapping.fs_line,
                   TaxTreatment(a.mapping.tax_treatment))
        for a in accounts
    ]
    inputs = get_inputs(s, tenant_id, client_id, year_end)
    allowances = parse_allowances(json.loads(inputs.allowances_json)) if inputs else []
    losses = Decimal(inputs.losses_brought_forward_cents) * CENT if inputs else ZERO
    leases = [y for lease in client_leases(s, tenant_id, client_id) if (y := lease_year(s, tenant_id, lease, year_end))]
    try:
        computation = compute(lines, allowances, leases, losses, KENYA)
    except ValueError as exc:
        raise Invalid(str(exc)) from exc
    return TaxRun(computation, accounts, leases, allowances)


def lead_schedule(accounts: list[MappedAccount]) -> list[tuple[str, Decimal, list[str]]]:
    """Each financial statement line with its total (natural sign) and the account codes in it."""
    out = []
    for key, line in FS_LINES.items():
        members = [a for a in accounts if a.mapping.fs_line == key]
        if members:
            total = sum((a.balance for a in members), ZERO)
            out.append((key, -total if line.credit_nature else total, [a.line.code for a in members]))
    return out
