"""Tax consultancy use cases: clients, trial balances, financial mapping and corporate tax computations."""
import hashlib
import json
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from provenant.api.auth import PARTNER, PREPARER, REVIEWER, Principal
from provenant.api.db import (
    AccountMappingEventRow,
    AccountMappingRow,
    ClientAttachmentRow,
    ClientRow,
    ClientTBLineRow,
    ClientTrialBalanceRow,
    Document,
    FixedAssetRegisterRow,
    FixedAssetRow,
    TaxInputsRow,
)
from provenant.api.services import MAX_UPLOAD_BYTES, Conflict, Invalid, NotFound, get_document, lease_schedule, require
from provenant.api.storage import BlobStore
from provenant.core import tb_csv
from provenant.core.money import ZERO, to_decimal
from provenant.covenants.engine import month_end
from provenant.tax import register as fa_register
from provenant.tax.engine import KENYA, AllowanceClass, Computation, LeaseYear, MappedLine, TaxCredit, compute
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
                cost_basis=to_decimal(str(a.get("cost_basis", "0") or "0")),
                disposals=to_decimal(str(a.get("disposals", "0") or "0")),
            )
            for a in raw
        ]
    except (KeyError, TypeError, ValueError) as exc:
        raise Invalid(f"capital deduction class: {exc}") from exc


def parse_credits(raw: list[dict]) -> list[TaxCredit]:
    try:
        return [
            TaxCredit(
                kind=str(c.get("kind", "")),
                description=str(c.get("description", "")).strip()[:200],
                amount=to_decimal(str(c.get("amount", "0"))),
                reference=str(c.get("reference", "") or "").strip()[:100],
                evidence=c.get("evidence") or None,
            )
            for c in raw
        ]
    except (TypeError, ValueError) as exc:
        raise Invalid(f"tax credit: {exc}") from exc


def _prior_return(raw: dict) -> dict:
    reference = str(raw.get("reference", "") or "").strip()[:100]
    filed_on = str(raw.get("filed_on", "") or "").strip()
    if filed_on:
        try:
            date.fromisoformat(filed_on)
        except ValueError as exc:
            raise Invalid("prior-year return: filing date must be a date like 2026-06-30") from exc
    return {"reference": reference, "filed_on": filed_on}


def set_inputs(
    s: Session, p: Principal, client_id: str, year_end: date, losses_brought_forward: str, allowances: list[dict],
    credits: list[dict] | None = None, prior_return: dict | None = None,
) -> TaxInputsRow:
    require(p, *ANY_ROLE)
    get_trial_balance(s, p.tenant_id, client_id, year_end)
    parse_allowances(allowances)  # validate before storing
    credits = credits or []
    evidence = {c.evidence for c in parse_credits(credits) if c.evidence}
    if evidence:
        known = set(s.scalars(select(ClientAttachmentRow.id).where(
            ClientAttachmentRow.tenant_id == p.tenant_id, ClientAttachmentRow.client_id == client_id,
            ClientAttachmentRow.id.in_(evidence),
        )))
        if evidence - known:
            raise Invalid("a credit points at a supporting document that is not this client's")
    prior = _prior_return(prior_return or {})
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
    row.credits_json = json.dumps(credits)
    row.prior_return_json = json.dumps(prior)
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
    credits: list[TaxCredit]
    from_register: set[str]  # allowance classes whose additions and disposals came from the register
    attachments: dict[str, str]  # attachment id -> filename, for credits' evidence


def year_start(year_end: date) -> date:
    return month_end(year_end.year - 1, year_end.month) + timedelta(days=1)


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
    credits = parse_credits(json.loads(inputs.credits_json)) if inputs else []
    losses = Decimal(inputs.losses_brought_forward_cents) * CENT if inputs else ZERO
    leases = [y for lease in client_leases(s, tenant_id, client_id) if (y := lease_year(s, tenant_id, lease, year_end))]

    # The register, when uploaded, supplies each matching class's additions and disposals.
    from_register: set[str] = set()
    unmatched: list[str] = []
    if (reg := get_register(s, tenant_id, client_id, year_end)) is not None:
        by_name = {c.asset_class.casefold(): c for c in register_summary(s, tenant_id, reg)}
        merged = []
        for a in allowances:
            moved = by_name.pop(a.name.casefold(), None)
            if moved:
                a = replace(a, additions=moved.additions, disposals=moved.disposals)
                from_register.add(a.name)
            merged.append(a)
        allowances, unmatched = merged, [c.asset_class for c in by_name.values()]
    try:
        computation = compute(lines, allowances, leases, losses, KENYA, credits)
    except ValueError as exc:
        raise Invalid(str(exc)) from exc
    if unmatched:
        note = (f"fixed asset register classes with no capital deduction class: {', '.join(unmatched)}"
                " (add a class with the same name, its rate and basis)")
        computation = replace(computation, warnings=(*computation.warnings, note))
    attachments = {a.id: a.filename for a in list_attachments(s, tenant_id, client_id, year_end)}
    return TaxRun(computation, accounts, leases, allowances, credits, from_register, attachments)


def lead_schedule(accounts: list[MappedAccount]) -> list[tuple[str, Decimal, list[str]]]:
    """Each financial statement line with its total (natural sign) and the account codes in it."""
    out = []
    for key, line in FS_LINES.items():
        members = [a for a in accounts if a.mapping.fs_line == key]
        if members:
            total = sum((a.balance for a in members), ZERO)
            out.append((key, -total if line.credit_nature else total, [a.line.code for a in members]))
    return out


# --- Fixed asset register -------------------------------------------------------------------------------------


def get_register(s: Session, tenant_id: str, client_id: str, year_end: date) -> FixedAssetRegisterRow | None:
    return s.scalar(select(FixedAssetRegisterRow).where(
        FixedAssetRegisterRow.tenant_id == tenant_id, FixedAssetRegisterRow.client_id == client_id,
        FixedAssetRegisterRow.year_end == year_end,
    ))


def register_assets(s: Session, tenant_id: str, reg: FixedAssetRegisterRow) -> list[fa_register.Asset]:
    rows = s.scalars(select(FixedAssetRow).where(
        FixedAssetRow.tenant_id == tenant_id, FixedAssetRow.register_id == reg.id
    ).order_by(FixedAssetRow.id)).all()
    return [
        fa_register.Asset(i, r.asset_code, r.description, r.asset_class, r.acquired_on,
                          Decimal(r.cost_cents) * CENT, r.disposed_on, Decimal(r.proceeds_cents) * CENT)
        for i, r in enumerate(rows, start=2)
    ]


def register_summary(s: Session, tenant_id: str, reg: FixedAssetRegisterRow) -> list[fa_register.ClassYear]:
    return fa_register.summarise(register_assets(s, tenant_id, reg), year_start(reg.year_end), reg.year_end)


def upload_register(
    s: Session, p: Principal, client_id: str, year_end: date, filename: str, data: bytes
) -> FixedAssetRegisterRow:
    """Store the register as at a year end, replacing any earlier upload for that year."""
    require(p, *ANY_ROLE)
    get_trial_balance(s, p.tenant_id, client_id, year_end)
    try:
        assets = fa_register.read(data)
        fa_register.summarise(assets, year_start(year_end), year_end)
    except ValueError as exc:
        raise Invalid(str(exc)) from exc
    if (old := get_register(s, p.tenant_id, client_id, year_end)) is not None:
        for row in s.scalars(select(FixedAssetRow).where(FixedAssetRow.register_id == old.id)):
            s.delete(row)
        s.delete(old)
        s.flush()
    reg = FixedAssetRegisterRow(
        tenant_id=p.tenant_id, client_id=client_id, year_end=year_end,
        filename=filename[:255] or "fixed-asset-register.csv", sha256=hashlib.sha256(data).hexdigest(),
        uploaded_by=p.user,
    )
    s.add(reg)
    s.flush()
    s.add_all(
        FixedAssetRow(tenant_id=p.tenant_id, register_id=reg.id, asset_code=a.code, description=a.description,
                      asset_class=a.asset_class, acquired_on=a.acquired, cost_cents=int(a.cost / CENT),
                      disposed_on=a.disposed, proceeds_cents=int(a.proceeds / CENT))
        for a in assets
    )
    s.commit()
    return reg


# --- Supporting documents -------------------------------------------------------------------------------------

ATTACHMENT_CATEGORIES = {
    "bank_statement": "Bank statement",
    "withholding_certificate": "Withholding tax certificate",
    "instalment_receipt": "Instalment tax receipt",
    "prior_return": "Prior-year return",
    "kra_correspondence": "KRA certificate or correspondence",
    "invoice": "Invoice or receipt",
    "asset_invoice": "Asset purchase or sale invoice",
    "payroll": "Payroll summary",
    "other": "Other",
}
# Extension -> (media type, leading bytes the file must start with, or None for UTF-8 text)
ATTACHMENT_TYPES = {
    "pdf": ("application/pdf", b"%PDF"),
    "png": ("image/png", b"\x89PNG"),
    "jpg": ("image/jpeg", b"\xff\xd8"),
    "jpeg": ("image/jpeg", b"\xff\xd8"),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", b"PK"),
    "csv": ("text/csv", None),
}


def upload_attachment(
    s: Session, p: Principal, store: BlobStore, client_id: str, year_end: date, category: str, filename: str,
    data: bytes, account_code: str | None = None, note: str = "",
) -> ClientAttachmentRow:
    require(p, *ANY_ROLE)
    get_client(s, p.tenant_id, client_id)
    if category not in ATTACHMENT_CATEGORIES:
        raise Invalid(f"category must be one of {', '.join(ATTACHMENT_CATEGORIES)}")
    if not data:
        raise Invalid("the file is empty")
    if len(data) > MAX_UPLOAD_BYTES:
        raise Invalid(f"file exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ATTACHMENT_TYPES:
        raise Invalid("supporting documents can be PDF, PNG, JPEG, XLSX or CSV files")
    media_type, magic = ATTACHMENT_TYPES[ext]
    if magic is None:
        try:
            data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise Invalid("a CSV file must be UTF-8 text") from exc
    elif not data.startswith(magic):
        raise Invalid(f"the file's contents are not a {ext.upper()} file")
    sha = hashlib.sha256(data).hexdigest()
    store.put(sha, data)
    row = ClientAttachmentRow(
        tenant_id=p.tenant_id, client_id=client_id, year_end=year_end, category=category,
        filename=filename[:255], sha256=sha, content_type=media_type, size_bytes=len(data),
        account_code=(account_code or "").strip()[:64] or None, note=note.strip()[:500], uploaded_by=p.user,
    )
    s.add(row)
    s.commit()
    return row


def list_attachments(s: Session, tenant_id: str, client_id: str, year_end: date) -> list[ClientAttachmentRow]:
    return list(s.scalars(select(ClientAttachmentRow).where(
        ClientAttachmentRow.tenant_id == tenant_id, ClientAttachmentRow.client_id == client_id,
        ClientAttachmentRow.year_end == year_end,
    ).order_by(ClientAttachmentRow.created_at)))


def get_attachment(s: Session, tenant_id: str, client_id: str, attachment_id: str) -> ClientAttachmentRow:
    row = s.scalar(select(ClientAttachmentRow).where(
        ClientAttachmentRow.tenant_id == tenant_id, ClientAttachmentRow.client_id == client_id,
        ClientAttachmentRow.id == attachment_id,
    ))
    if row is None:
        raise NotFound("supporting document not found")
    return row


def delete_attachment(s: Session, p: Principal, client_id: str, attachment_id: str) -> None:
    require(p, *ANY_ROLE)
    row = get_attachment(s, p.tenant_id, client_id, attachment_id)
    inputs = get_inputs(s, p.tenant_id, client_id, row.year_end)
    if inputs and any(c.get("evidence") == row.id for c in json.loads(inputs.credits_json)):
        raise Conflict("this document is the evidence for a tax credit: remove it from the credit first")
    s.delete(row)
    s.commit()


# --- Prior-year roll forward ----------------------------------------------------------------------------------


def roll_forward(s: Session, p: Principal, client_id: str, year_end: date) -> dict:
    """Propose this year's opening position from last year's computation. Nothing is saved: a person reviews it."""
    require(p, *ANY_ROLE)
    prior_end = month_end(year_end.year - 1, year_end.month)
    try:
        prior = tax_computation(s, p.tenant_id, client_id, prior_end)
    except NotFound as exc:
        raise Conflict(
            f"no computation for the year ended {prior_end}: enter the figures from the prior-year return instead"
        ) from exc
    c = prior.computation
    current = get_inputs(s, p.tenant_id, client_id, year_end)
    credits = [x for x in (json.loads(current.credits_json) if current else []) if x.get("kind") != "overpayment"]
    if c.tax_due < 0:
        credits.insert(0, {"kind": "overpayment", "description": f"Refund due per the computation for {prior_end}",
                           "amount": str(-c.tax_due), "reference": "", "evidence": None})
    return {
        "losses_brought_forward": str(c.losses_carried_forward),
        "allowances": [
            {"name": a.name, "wdv_brought_forward": str(a.wdv_carried_forward()), "additions": "0",
             "disposals": "0", "rate_percent": format((a.rate * 100).normalize(), "f"), "method": a.method,
             "basis": a.basis, "cost_basis": str(a.cost_basis + a.additions) if a.method == "straight_line" else "0"}
            for a in prior.allowances
        ],
        "credits": credits,
        "prior_return": json.loads(current.prior_return_json) if current else {"reference": "", "filed_on": ""},
    }
