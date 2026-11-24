"""To review: everything in the firm waiting for a person, in one list (ADR 0021).

Provenant prepares and people decide. This list gathers what is waiting for a decision across documents, contracts,
matters and the trust account, says who prepared each item (a person, or Claude), and whether the person looking can
act on it: nobody decides what they prepared themselves.
"""
import json
from collections import defaultdict
from datetime import date, timedelta

from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from provenant.api.auth import PARTNER, REVIEWER, Principal
from provenant.api.db import (
    ClientRow,
    ClientTrialBalanceRow,
    ContractDraftRow,
    ContractFindingRow,
    ContractReviewRow,
    CourtNoticeRow,
    DocketEntryRow,
    Document,
    FieldRow,
    MatterRow,
    PrecedentDraftRow,
    PrecedentRow,
    TrustAccountRow,
    TrustReconciliationRow,
)
from provenant.api.matters import matter_gaps
from provenant.api.tax import mapped_accounts
from provenant.provenance import Status

SOON = timedelta(days=14)  # deadlines due within this many days are listed


def _day(d: date) -> str:
    return f"{d.day} {d:%b %Y}"


def _item(kind: str, title: str, detail: str, href: str, *, prepared_by: str = "", by_claude: bool = False,
          yours: bool = True, why_not: str = "", due: date | None = None, urgent: bool = False) -> dict:
    return {"kind": kind, "title": title, "detail": detail, "href": href, "prepared_by": prepared_by,
            "by_claude": by_claude, "yours": yours, "why_not": why_not,
            "due": due.isoformat() if due else None, "urgent": urgent}


def items(s: Session, p: Principal, today: date) -> list[dict]:
    t, me = p.tenant_id, p.user
    checker = bool({REVIEWER, PARTNER} & set(p.roles))
    out: list[dict] = []
    docs = {d.id: d for d in s.scalars(select(Document).where(Document.tenant_id == t))}

    # Deadlines first: overdue or due soon.
    for e, ref in s.execute(select(DocketEntryRow, MatterRow.reference).join(MatterRow, MatterRow.id == DocketEntryRow.matter_id)
                            .where(DocketEntryRow.tenant_id == t, DocketEntryRow.status == "open",
                                   DocketEntryRow.due_on <= today + SOON).order_by(DocketEntryRow.due_on)):
        late = e.due_on < today
        out.append(_item("deadline", e.title, f"{ref} · {'overdue' if late else 'due'} {_day(e.due_on)}",
                         f"/matters/{e.matter_id}", due=e.due_on, urgent=late or e.due_on <= today + timedelta(days=3)))

    # Figures read from documents that a person has not checked.
    waiting = s.execute(select(FieldRow.document_id, func.count()).where(
        FieldRow.tenant_id == t, FieldRow.status == Status.NEEDS_REVIEW.value).group_by(FieldRow.document_id)).all()
    for doc_id, n in waiting:
        d = docs.get(doc_id)
        if d is None:
            continue
        own = d.uploaded_by == me
        out.append(_item("figures", f"Check {n} figure{'s' if n != 1 else ''} read from {d.filename}",
                         "Each figure links to the box on the page it came from.", f"/documents/{doc_id}",
                         prepared_by=d.uploaded_by, yours=not own, why_not="you uploaded it" if own else ""))

    # Contract findings no lawyer has decided.
    agents = dict(s.execute(select(ContractReviewRow.id, ContractReviewRow.agent).where(ContractReviewRow.tenant_id == t)).all())
    by_doc: dict[str, list] = defaultdict(list)
    for f in s.scalars(select(ContractFindingRow).where(ContractFindingRow.tenant_id == t,
                                                        ContractFindingRow.decision.is_(None))):
        by_doc[f.document_id].append(f)
    for doc_id, found in by_doc.items():
        high = sum(1 for f in found if f.severity == "high")
        claude = any((agents.get(f.review_id) or "").startswith("claude") for f in found)
        out.append(_item("findings", f"Decide {len(found)} contract finding{'s' if len(found) != 1 else ''}",
                         f"{found[0].filename}{f' · {high} high' if high else ''}", f"/documents/{doc_id}?tab=review",
                         prepared_by="Claude" if claude else "keyword reader", by_claude=claude,
                         yours=checker, why_not="" if checker else "a reviewer or partner decides", urgent=high > 0))

    # Contract drafts ready for approval.
    for d in s.scalars(select(ContractDraftRow).where(ContractDraftRow.tenant_id == t, ContractDraftRow.status == "ready")):
        own = d.requested_by == me
        out.append(_item("draft", f"Approve draft: {d.title or d.kind}", docs[d.document_id].filename if d.document_id in docs else "",
                         f"/documents/{d.document_id}?tab=drafts", prepared_by=d.requested_by,
                         by_claude=(d.agent or "").startswith("claude"), yours=checker and not own,
                         why_not="you asked for it" if own else "" if checker else "a reviewer or partner approves"))

    # Trial balance accounts whose mapping no person has confirmed (the tax computation waits for them).
    latest: dict[str, date] = {}
    for client_id, year_end in s.execute(select(ClientTrialBalanceRow.client_id, ClientTrialBalanceRow.year_end)
                                         .where(ClientTrialBalanceRow.tenant_id == t)):
        latest[client_id] = max(year_end, latest.get(client_id, year_end))
    clients = dict(s.execute(select(ClientRow.id, ClientRow.name).where(ClientRow.tenant_id == t)).all())
    for client_id, year_end in latest.items():
        open_ = [a for a in mapped_accounts(s, t, client_id, year_end)
                 if a.mapping.status != "confirmed" and a.line.balance_cents != 0]
        if open_:
            claude = any((a.mapping.rule or "").startswith("claude: ") for a in open_)
            out.append(_item("mapping", f"Confirm {len(open_)} account mapping{'s' if len(open_) != 1 else ''}",
                             f"{clients.get(client_id, 'Client')} · year ended {_day(year_end)}",
                             f"/clients/{client_id}?tab=mapping&year={year_end.isoformat()}",
                             prepared_by="Claude" if claude else "", by_claude=claude))

    matters = {m.id: m for m in s.scalars(select(MatterRow).where(MatterRow.tenant_id == t))}

    # New matters waiting for their conflict check to be cleared.
    for m in matters.values():
        if m.status == "conflict_review":
            own = m.created_by == me
            out.append(_item("conflict", f"Clear the conflict check: {m.title}", m.reference, f"/matters/{m.id}",
                             prepared_by=m.created_by, yours=not own, why_not="you opened it" if own else ""))

    # Court notices read but not confirmed.
    for n in s.scalars(select(CourtNoticeRow).where(CourtNoticeRow.tenant_id == t, CourtNoticeRow.status == "new")):
        m = matters.get(n.matter_id)
        reading = json.loads(n.reading)
        event = reading.get("event") or "an event"
        out.append(_item("notice", f"Confirm a court notice ({event})", m.reference if m else "", f"/matters/{n.matter_id}",
                         prepared_by="Claude" if reading.get("reader") == "claude" else "rules reader",
                         by_claude=reading.get("reader") == "claude", urgent=True))

    # Drafts from precedents waiting for approval.
    names = dict(s.execute(select(PrecedentRow.id, PrecedentRow.name).where(PrecedentRow.tenant_id == t)).all())
    for d in s.scalars(select(PrecedentDraftRow).where(PrecedentDraftRow.tenant_id == t, PrecedentDraftRow.status == "ready")):
        own = me in (d.created_by, d.edited_by)
        m = matters.get(d.matter_id)
        out.append(_item("precedent", f"Approve {names.get(d.precedent_id, 'precedent draft')}", m.reference if m else "",
                         f"/matters/{d.matter_id}", prepared_by=d.edited_by or d.created_by, yours=checker and not own,
                         why_not="you made or edited it" if own else "" if checker else "a reviewer or partner approves"))

    # Trust reconciliations to sign off or fix.
    accounts = dict(s.execute(select(TrustAccountRow.id, TrustAccountRow.name).where(TrustAccountRow.tenant_id == t)).all())
    for r in s.scalars(select(TrustReconciliationRow).where(TrustReconciliationRow.tenant_id == t,
                                                            TrustReconciliationRow.approved_by.is_(None))):
        name = accounts.get(r.account_id, "Client account")
        if r.balanced:
            own = r.prepared_by == me
            out.append(_item("trust", f"Sign off the {name} reconciliation", f"as at {_day(r.as_of)}", "/trust",
                             prepared_by=r.prepared_by, yours=checker and not own,
                             why_not="you prepared it" if own else "" if checker else "a reviewer or partner signs off"))
        else:
            out.append(_item("trust", f"Fix the breaks in the {name} reconciliation", f"as at {_day(r.as_of)}",
                             "/trust", prepared_by=r.prepared_by, urgent=True))

    # Open matters with KYC gaps.
    gaps = [(m, matter_gaps(s, m, today)) for m in matters.values() if m.status == "open"]
    gaps = [(m, g) for m, g in gaps if g]
    if gaps:
        total = sum(len(g) for _, g in gaps)
        out.append(_item("kyc", f"Collect {total} missing KYC item{'s' if total != 1 else ''}",
                         f"{len(gaps)} open matter{'s' if len(gaps) != 1 else ''}: " + ", ".join(m.reference for m, _ in gaps[:4]),
                         "/matters"))

    order = {True: 0, False: 1}
    return sorted(out, key=lambda x: (x["kind"] != "deadline" or not x["urgent"], order[x["yours"]], not x["urgent"],
                                      x["due"] or "9999"))


def add_routes(app: FastAPI, Auth, Db) -> None:  # noqa: N803 - FastAPI dependency aliases
    @app.get("/todo")
    def todo(p: Auth, s: Db) -> dict:
        found = items(s, p, date.today())
        return {"items": found, "yours": sum(1 for x in found if x["yours"]),
                "waiting_on_others": sum(1 for x in found if not x["yours"])}
