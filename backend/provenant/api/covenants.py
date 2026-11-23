"""Credit agreement use cases: trial balance upload and covenant runs."""
import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from provenant.api.auth import PARTNER, PREPARER, REVIEWER, Principal
from provenant.api.db import (
    CovenantAdjustmentRow,
    CovenantPeriodInputRow,
    Document,
    FieldRow,
    TrialBalanceLineRow,
    TrialBalanceRow,
)
from provenant.api.services import (
    Conflict,
    Forbidden,
    Invalid,
    NotFound,
    as_csv,
    get_document,
    lease_schedule,
    list_fields,
    require,
)
from provenant.core import tb_csv
from provenant.core.money import to_decimal
from provenant.covenants import Category, PeriodInput, TBLine, Terms, TestDate, TrialBalance, run
from provenant.covenants.engine import Adjustment, FrozenLease, month_end

MAX_TB_BYTES = tb_csv.MAX_BYTES
CENT = Decimal("0.01")
MAX_DRAWN = Decimal(10) ** 15


def _credit_agreement(s: Session, tenant_id: str, document_id: str) -> Document:
    doc = get_document(s, tenant_id, document_id)
    if doc.kind != "credit_agreement":
        raise Conflict("trial balances and covenants belong to a credit agreement")
    return doc


def parse_trial_balance(data: bytes, period_end: date) -> TrialBalance:
    """CSV with columns code, name, category and either balance (debit positive) or debit and credit.

    Every line must be mapped to a known category, and the trial balance must balance to the cent.
    """
    try:
        rows = tb_csv.read(data, require_category=True)
    except ValueError as exc:
        raise Invalid(str(exc)) from exc
    categories = {c.value for c in Category}
    lines = []
    for row in rows:
        if row.category not in categories:
            raise Invalid(f"row {row.number}: category {row.category!r} is not one of {', '.join(sorted(categories))}")
        lines.append(TBLine(row.code, row.name, Category(row.category), row.balance))
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
    tb = parse_trial_balance(as_csv(data, filename), period_end)
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
            elif t.cure_pending:
                out.append(Alert("breach", t.certificate_due, f"{names} breached at {quarter}; the cure window runs "
                                                              "from delivery: enter and confirm the delivery date"))
            else:
                out.append(Alert("breach", t.certificate_due, f"{names} breached at {quarter}; no cure right in the agreement"))
        for r in t.results:
            if r.status == "warning":
                out.append(Alert("warning", t.period_end, f"{KIND_LABELS[r.covenant.kind]} within the warning headroom at {quarter}"))
            elif r.status == "undetermined":
                severity = "overdue" if t.certificate_due < as_of else "warning"
                out.append(Alert(severity, t.certificate_due,
                                 f"{KIND_LABELS[r.covenant.kind]} at {quarter} cannot be determined: {r.note}"))
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
    period_inputs: list[CovenantPeriodInputRow]
    adjustments: list[CovenantAdjustmentRow]


def _terms(s: Session, tenant_id: str, document_id: str) -> tuple[Terms, list[FieldRow]]:
    rows = list_fields(s, tenant_id, document_id)
    try:
        return Terms.from_fields({r.name: r.to_domain() for r in rows}), rows
    except (PermissionError, ValueError) as exc:
        raise Conflict(str(exc)) from exc


def _frozen_leases(s: Session, tenant_id: str, rows: list[CovenantAdjustmentRow]) -> tuple[list[FrozenLease], str | None]:
    """The approved leases' verified schedules. A lease that cannot be spread reliably is a stated problem, so the
    ratios it feeds are undetermined. shortcut: amended leases are not respread on an operating lease basis yet."""
    leases: list[FrozenLease] = []
    problem = None
    for row in rows:
        try:
            chained = lease_schedule(s, tenant_id, row.lease_document_id)
        except Conflict as exc:
            problem = problem or f"lease {row.label}: {exc}"
            continue
        if chained.amendments or chained.separate:
            problem = problem or f"lease {row.label} has amendments, which frozen GAAP does not cover yet"
            continue
        leases.append(FrozenLease(row.label, row.lease_document_id, tuple(
            (p.end_date, p.payment, p.interest, p.closing) for p in chained.schedule.periods)))
    return leases, problem


def covenant_run(s: Session, tenant_id: str, document_id: str, as_of: date) -> CovenantRun:
    """Every test date from the first test date to a year past `as_of`, tested where trial balances allow."""
    doc = _credit_agreement(s, tenant_id, document_id)
    terms, rows = _terms(s, tenant_id, doc.id)

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
    input_rows = _period_inputs(s, tenant_id, doc.id)
    confirmed = {
        r.period_end: PeriodInput(
            None if r.revolver_drawn_cents is None else Decimal(r.revolver_drawn_cents) * CENT, r.delivered_on)
        for r in input_rows if r.confirmed_by
    }
    adjustment_rows = _adjustments(s, tenant_id, doc.id)
    approved = [r for r in adjustment_rows if r.approved_by]
    addbacks = [Adjustment(r.quarter_end, r.label, Decimal(r.amount_cents) * CENT, r.capped, r.id)
                for r in approved if r.kind == "addback"]
    leases, lease_problem = ([], None)
    if terms.frozen_gaap:
        leases, lease_problem = _frozen_leases(s, tenant_id, [r for r in approved if r.kind == "frozen_lease"])
    return CovenantRun(
        terms=terms,
        fields=rows,
        test_dates=run(terms, trial_balances, horizon, confirmed, addbacks, leases, lease_problem),
        trial_balances={r.period_end: r for r in tb_rows},
        as_of=as_of,
        period_inputs=input_rows,
        adjustments=adjustment_rows,
    )


def _adjustments(s: Session, tenant_id: str, document_id: str) -> list[CovenantAdjustmentRow]:
    return list(s.scalars(
        select(CovenantAdjustmentRow)
        .where(CovenantAdjustmentRow.tenant_id == tenant_id, CovenantAdjustmentRow.document_id == document_id)
        .order_by(CovenantAdjustmentRow.quarter_end, CovenantAdjustmentRow.entered_at)
    ))


def _adjustment(s: Session, tenant_id: str, document_id: str, adjustment_id: str) -> CovenantAdjustmentRow:
    row = s.scalar(select(CovenantAdjustmentRow).where(
        CovenantAdjustmentRow.tenant_id == tenant_id, CovenantAdjustmentRow.document_id == document_id,
        CovenantAdjustmentRow.id == adjustment_id))
    if row is None:
        raise NotFound("adjustment not found")
    return row


def add_adjustment(s: Session, p: Principal, document_id: str, kind: str, clause: str, basis: str,
                   quarter_end: date | None = None, label: str = "", amount: str | None = None,
                   capped: bool = False, lease_document_id: str | None = None) -> CovenantAdjustmentRow:
    """Record an add-back, a deduction or a frozen-GAAP lease. It has no effect until a partner approves it."""
    require(p, PREPARER, REVIEWER, PARTNER)
    doc = _credit_agreement(s, p.tenant_id, document_id)
    terms, _ = _terms(s, p.tenant_id, doc.id)
    clause, basis, label = " ".join(clause.split()), " ".join(basis.split()), " ".join(label.split())
    if len(clause) < 3:
        raise Invalid("name the clause of the agreement that allows this")
    if len(basis) < 10:
        raise Invalid("state the basis (e.g. the invoice, board paper or lender's confirmation)")
    row = CovenantAdjustmentRow(tenant_id=p.tenant_id, document_id=doc.id, kind=kind, clause=clause[:255],
                                basis=basis[:1000], entered_by=p.user, entered_at=datetime.now(timezone.utc))
    if kind == "addback":
        if quarter_end is None or not terms.on_test_cycle(quarter_end):
            raise Invalid("the quarter must be a quarter-end of the agreement's testing cycle")
        if len(label) < 3:
            raise Invalid("describe the item")
        try:
            value = to_decimal(amount or "")
            exact = value != 0 and abs(value) < MAX_DRAWN and value == value.quantize(CENT)
        except (ValueError, ArithmeticError) as exc:
            raise Invalid(f"amount: {exc}") from exc
        if not exact:
            raise Invalid("the amount must be non-zero and to the cent; a deduction is negative")
        if capped and value < 0:
            raise Invalid("a cap limits add-backs, so a deduction cannot be capped")
        row.quarter_end, row.label, row.amount_cents, row.capped = quarter_end, label[:255], int(value / CENT), capped
    elif kind == "frozen_lease":
        if not terms.frozen_gaap:
            raise Conflict("the agreement's lease treatment is not frozen GAAP")
        lease = get_document(s, p.tenant_id, lease_document_id or "")
        if lease.kind != "lease":
            raise Invalid("choose a lease")
        row.lease_document_id, row.label = lease.id, lease.filename[:255]
    else:
        raise Invalid("the adjustment must be an add-back or a frozen-GAAP lease")
    s.add(row)
    try:
        s.commit()
    except IntegrityError as exc:
        s.rollback()
        raise Conflict("that lease is already on this agreement") from exc
    return row


def approve_adjustment(s: Session, p: Principal, document_id: str, adjustment_id: str) -> CovenantAdjustmentRow:
    require(p, PARTNER)
    doc = _credit_agreement(s, p.tenant_id, document_id)
    row = _adjustment(s, p.tenant_id, doc.id, adjustment_id)
    if p.user in (row.entered_by, doc.uploaded_by):
        raise Forbidden("someone other than the person who entered it, or uploaded the agreement, must approve it")
    if row.approved_by:
        raise Conflict(f"already approved by {row.approved_by}")
    row.approved_by, row.approved_at = p.user, datetime.now(timezone.utc)
    s.commit()
    return row


def delete_adjustment(s: Session, p: Principal, document_id: str, adjustment_id: str) -> None:
    require(p, PREPARER, REVIEWER, PARTNER)
    doc = _credit_agreement(s, p.tenant_id, document_id)
    row = _adjustment(s, p.tenant_id, doc.id, adjustment_id)
    if not p.has_any(PARTNER) and (row.approved_by or row.entered_by != p.user):
        raise Forbidden("only a partner can remove an approved adjustment or someone else's entry")
    s.delete(row)
    s.commit()


def _period_inputs(s: Session, tenant_id: str, document_id: str) -> list[CovenantPeriodInputRow]:
    return list(s.scalars(
        select(CovenantPeriodInputRow)
        .where(CovenantPeriodInputRow.tenant_id == tenant_id, CovenantPeriodInputRow.document_id == document_id)
        .order_by(CovenantPeriodInputRow.period_end)
    ))


def _period_input(s: Session, tenant_id: str, document_id: str, period_end: date) -> CovenantPeriodInputRow | None:
    return s.scalar(select(CovenantPeriodInputRow).where(
        CovenantPeriodInputRow.tenant_id == tenant_id, CovenantPeriodInputRow.document_id == document_id,
        CovenantPeriodInputRow.period_end == period_end))


def save_period_input(s: Session, p: Principal, document_id: str, period_end: date, revolver_drawn: str | None,
                      delivered_on: date | None, basis: str) -> CovenantPeriodInputRow:
    """Enter or replace a test date's facts. A change always needs confirming again."""
    require(p, PREPARER, REVIEWER, PARTNER)
    doc = _credit_agreement(s, p.tenant_id, document_id)
    if period_end != month_end(period_end.year, period_end.month):
        raise Invalid("the period end must be a month-end")
    basis = " ".join(basis.split())
    if len(basis) < 10:
        raise Invalid("state the basis (e.g. the facility statement or the lender's acknowledgement)")
    drawn_text = (revolver_drawn or "").strip()
    if not drawn_text and delivered_on is None:
        raise Invalid("enter the revolver drawn, the delivery date, or both")
    cents = None
    if drawn_text:
        try:
            drawn = to_decimal(drawn_text)
            exact = 0 <= drawn < MAX_DRAWN and drawn == drawn.quantize(CENT)
        except (ValueError, ArithmeticError) as exc:
            raise Invalid(f"revolver drawn: {exc}") from exc
        if not exact:
            raise Invalid("revolver drawn must be a positive amount to the cent")
        cents = int(drawn / CENT)
    if delivered_on is not None and delivered_on < period_end:
        raise Invalid("a certificate cannot be delivered before its quarter ends")
    row = _period_input(s, p.tenant_id, doc.id, period_end)
    if row is None:
        row = CovenantPeriodInputRow(tenant_id=p.tenant_id, document_id=doc.id, period_end=period_end)
        s.add(row)
    row.revolver_drawn_cents, row.delivered_on, row.basis = cents, delivered_on, basis[:1000]
    row.entered_by, row.entered_at = p.user, datetime.now(timezone.utc)
    row.confirmed_by, row.confirmed_at = None, None
    s.commit()
    return row


def confirm_period_input(s: Session, p: Principal, document_id: str, period_end: date) -> CovenantPeriodInputRow:
    require(p, REVIEWER, PARTNER)
    doc = _credit_agreement(s, p.tenant_id, document_id)
    row = _period_input(s, p.tenant_id, doc.id, period_end)
    if row is None:
        raise NotFound("nothing entered for that test date")
    if p.user in (row.entered_by, doc.uploaded_by):
        raise Forbidden("someone other than the person who entered it, or uploaded the agreement, must confirm it")
    if row.confirmed_by:
        raise Conflict(f"already confirmed by {row.confirmed_by}")
    row.confirmed_by, row.confirmed_at = p.user, datetime.now(timezone.utc)
    s.commit()
    return row
