"""Credit agreement use cases: trial balance upload and covenant runs."""
import csv
import hashlib
import io
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from provenant.api.auth import PARTNER, PREPARER, REVIEWER, Principal
from provenant.api.db import Document, FieldRow, TrialBalanceLineRow, TrialBalanceRow
from provenant.api.services import Conflict, Forbidden, Invalid, NotFound, get_document, list_fields, require
from provenant.core.money import round_money, to_decimal
from provenant.covenants import Category, TBLine, Terms, TestDate, TrialBalance, run
from provenant.covenants.engine import month_end

MAX_TB_BYTES = 2 * 1024 * 1024
MAX_TB_LINES = 20_000
CENT = Decimal("0.01")


def _credit_agreement(s: Session, tenant_id: str, document_id: str) -> Document:
    doc = get_document(s, tenant_id, document_id)
    if doc.kind != "credit_agreement":
        raise Conflict("trial balances and covenants belong to a credit agreement")
    return doc


def parse_trial_balance(data: bytes, period_end: date) -> TrialBalance:
    """CSV with columns code, name, category and either balance (debit positive) or debit and credit.

    Every line must be mapped to a known category, and the trial balance must balance to the cent.
    """
    if len(data) > MAX_TB_BYTES:
        raise Invalid("trial balance file exceeds 2 MB")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise Invalid("trial balance must be a UTF-8 CSV file") from exc
    reader = csv.DictReader(io.StringIO(text))
    headers = {h.strip().lower() for h in reader.fieldnames or []}
    if not {"code", "name", "category"} <= headers or not ({"balance"} <= headers or {"debit", "credit"} <= headers):
        raise Invalid("columns needed: code, name, category, and balance (or debit and credit)")

    categories = {c.value for c in Category}
    lines = []
    for number, raw in enumerate(reader, start=2):  # row 1 is the header
        row = {k.strip().lower(): (v or "").strip() for k, v in raw.items() if k}
        if not any(row.values()):
            continue
        if len(lines) >= MAX_TB_LINES:
            raise Invalid(f"trial balance has more than {MAX_TB_LINES} lines")
        category = row["category"].lower()
        if category not in categories:
            raise Invalid(f"row {number}: category {row['category']!r} is not one of {', '.join(sorted(categories))}")
        try:
            if "balance" in row and row["balance"]:
                balance = to_decimal(row["balance"])
            else:
                balance = to_decimal(row.get("debit") or "0") - to_decimal(row.get("credit") or "0")
        except (ValueError, TypeError) as exc:
            raise Invalid(f"row {number}: {exc}") from exc
        if round_money(balance) != balance:
            raise Invalid(f"row {number}: balance has more than two decimal places")
        lines.append(TBLine(row["code"][:64], row["name"][:255], Category(category), balance))
    if not lines:
        raise Invalid("trial balance has no lines")
    if period_end != month_end(period_end.year, period_end.month):
        raise Invalid("the period end must be a month-end")
    try:
        return TrialBalance(period_end, tuple(lines))
    except ValueError as exc:
        raise Invalid(str(exc)) from exc


def upload_trial_balance(
    s: Session, p: Principal, document_id: str, period_end: date, filename: str, data: bytes
) -> TrialBalanceRow:
    require(p, PREPARER, REVIEWER, PARTNER)
    doc = _credit_agreement(s, p.tenant_id, document_id)
    tb = parse_trial_balance(data, period_end)
    row = TrialBalanceRow(
        tenant_id=p.tenant_id,
        document_id=doc.id,
        period_end=period_end,
        filename=filename[:255] or "trial-balance.csv",
        sha256=hashlib.sha256(data).hexdigest(),
        uploaded_by=p.user,
    )
    s.add(row)
    try:
        s.flush()
    except IntegrityError as exc:
        s.rollback()
        raise Conflict(f"a trial balance for {period_end} is already uploaded; delete it to replace it") from exc
    s.add_all(
        TrialBalanceLineRow(
            tenant_id=p.tenant_id,
            trial_balance_id=row.id,
            code=line.code,
            name=line.name,
            category=line.category.value,
            balance_cents=int(line.balance / CENT),
        )
        for line in tb.lines
    )
    s.commit()
    return row


def list_trial_balances(s: Session, tenant_id: str, document_id: str) -> list[tuple[TrialBalanceRow, int]]:
    _credit_agreement(s, tenant_id, document_id)
    rows = s.scalars(
        select(TrialBalanceRow)
        .where(TrialBalanceRow.tenant_id == tenant_id, TrialBalanceRow.document_id == document_id)
        .order_by(TrialBalanceRow.period_end)
    ).all()
    counts = dict(
        s.execute(
            select(TrialBalanceLineRow.trial_balance_id, func.count())
            .where(TrialBalanceLineRow.tenant_id == tenant_id, TrialBalanceLineRow.trial_balance_id.in_([r.id for r in rows]))
            .group_by(TrialBalanceLineRow.trial_balance_id)
        ).all()
    )
    return [(r, counts.get(r.id, 0)) for r in rows]


def delete_trial_balance(s: Session, p: Principal, trial_balance_id: str) -> None:
    require(p, PREPARER, REVIEWER, PARTNER)
    row = s.scalar(
        select(TrialBalanceRow).where(TrialBalanceRow.tenant_id == p.tenant_id, TrialBalanceRow.id == trial_balance_id)
    )
    if row is None:
        raise NotFound("trial balance not found")
    if p.user != row.uploaded_by and not p.has_any(PARTNER):
        raise Forbidden("only the person who uploaded a trial balance, or a partner, can delete it")
    s.execute(delete(TrialBalanceLineRow).where(TrialBalanceLineRow.trial_balance_id == row.id))
    s.delete(row)
    s.commit()


def _lines(s: Session, tenant_id: str, trial_balance_id: str) -> list[TrialBalanceLineRow]:
    return list(
        s.scalars(
            select(TrialBalanceLineRow).where(
                TrialBalanceLineRow.tenant_id == tenant_id, TrialBalanceLineRow.trial_balance_id == trial_balance_id
            )
        )
    )


@dataclass(frozen=True)
class Alert:
    severity: str  # breach | overdue | warning | due
    on: date
    message: str


ALERT_WINDOW = timedelta(days=45)
KIND_LABELS = {"leverage": "Leverage", "interest_cover": "Interest cover", "min_liquidity": "Minimum liquidity"}


def status_of(t: TestDate, as_of: date) -> str:
    if t.results:
        return "tested"
    return "overdue" if t.certificate_due < as_of else "awaiting"


def alerts(test_dates: list[TestDate], as_of: date) -> list[Alert]:
    """What needs attention as of a date: open cure windows, missing data past its deadline, results inside the
    warning headroom, and compliance certificates falling due soon. Most urgent first."""
    out: list[Alert] = []
    for t in test_dates:
        quarter = t.period_end.isoformat()
        breached = [r for r in t.results if r.status == "breach"]
        if breached and (t.cure_deadline is None or t.cure_deadline >= as_of):
            names = ", ".join(KIND_LABELS[r.covenant.kind] for r in breached)
            if t.cure_deadline:
                out.append(Alert("breach", t.cure_deadline, f"{names} breached at {quarter}; cure window closes {t.cure_deadline}"))
            else:
                out.append(Alert("breach", t.certificate_due, f"{names} breached at {quarter}; no cure right in the agreement"))
        for r in t.results:
            if r.status == "warning":
                out.append(Alert("warning", t.period_end, f"{KIND_LABELS[r.covenant.kind]} within the warning headroom at {quarter}"))
        status = status_of(t, as_of)
        if status == "overdue":
            out.append(Alert("overdue", t.certificate_due, f"Certificate for {quarter} was due {t.certificate_due}: {t.missing}"))
        elif status == "awaiting" and t.certificate_due - as_of <= ALERT_WINDOW:
            out.append(Alert("due", t.certificate_due, f"Certificate for {quarter} due {t.certificate_due}: {t.missing}"))
    rank = {"breach": 0, "overdue": 1, "warning": 2, "due": 3}
    return sorted(out, key=lambda a: (rank[a.severity], a.on))


@dataclass(frozen=True)
class CovenantRun:
    terms: Terms
    fields: list[FieldRow]
    test_dates: list[TestDate]
    trial_balances: dict[date, TrialBalanceRow]
    as_of: date


def covenant_run(s: Session, tenant_id: str, document_id: str, as_of: date) -> CovenantRun:
    """Every test date from the first test date to a year past `as_of`, tested where trial balances allow."""
    doc = _credit_agreement(s, tenant_id, document_id)
    rows = list_fields(s, tenant_id, doc.id)
    try:
        terms = Terms.from_fields({r.name: r.to_domain() for r in rows})
    except (PermissionError, ValueError) as exc:
        raise Conflict(str(exc)) from exc

    tb_rows = s.scalars(
        select(TrialBalanceRow).where(TrialBalanceRow.tenant_id == tenant_id, TrialBalanceRow.document_id == doc.id)
    ).all()
    trial_balances = [
        TrialBalance(
            r.period_end,
            tuple(
                TBLine(line.code, line.name, Category(line.category), Decimal(line.balance_cents) * CENT)
                for line in _lines(s, tenant_id, r.id)
            ),
        )
        for r in tb_rows
    ]
    horizon = max([as_of + timedelta(days=365), *[r.period_end for r in tb_rows]])
    return CovenantRun(
        terms=terms,
        fields=rows,
        test_dates=run(terms, trial_balances, horizon),
        trial_balances={r.period_end: r for r in tb_rows},
        as_of=as_of,
    )
