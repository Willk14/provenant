"""Matters, conflict checks, KYC file items, practising certificates and the file audit (ADR 0018)."""
import csv
import hashlib
import io
import json
import re
from dataclasses import asdict
from datetime import UTC, date, datetime
from typing import Annotated, Literal

from fastapi import FastAPI, File, Form, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from provenant.api.auth import PARTNER, PREPARER, REVIEWER, Principal
from provenant.api.db import (
    ConflictCheckRow,
    MatterEventRow,
    MatterPartyRow,
    MatterRequirementRow,
    MatterRow,
    MemberRow,
    PractisingCertificateRow,
    TrustEntryRow,
    WatchlistEntryRow,
)
from provenant.api import practice_ai
from provenant.api.inbox import _allowed
from provenant.api.services import MAX_UPLOAD_BYTES, Conflict, Forbidden, Invalid, NotFound, require
from provenant.api.storage import BlobStore
from provenant.practice import completeness as kyc
from provenant.practice.conflicts import Known, Party, _kind, check, similarity

ANY = (PREPARER, REVIEWER, PARTNER)
ROLES = ("client", "counterparty", "related", "beneficial_owner")
MAX_PARTIES = 40
MAX_WATCHLIST = 50_000


def _event(s: Session, m: MatterRow, action: str, actor: str, note: str | None = None) -> None:
    s.add(MatterEventRow(tenant_id=m.tenant_id, matter_id=m.id, action=action, actor=actor, note=note))


def get_matter(s: Session, tenant_id: str, matter_id: str) -> MatterRow:
    row = s.scalar(select(MatterRow).where(MatterRow.tenant_id == tenant_id, MatterRow.id == matter_id))
    if row is None:
        raise NotFound("matter not found")
    return row


def _next_reference(s: Session, tenant_id: str) -> str:
    year = datetime.now(UTC).year
    count = s.scalar(select(func.count()).select_from(MatterRow).where(
        MatterRow.tenant_id == tenant_id, MatterRow.reference.like(f"M-{year}-%"))) or 0
    return f"M-{year}-{count + 1:03d}"


SECOND_LOOK_NAMES = 400  # known names sent to Claude at most, closest first


def _second_look(s: Session, tenant_id: str, parties: list[Party], known: list[Known], hits: list[dict]) -> list[dict]:
    """Claude looks for matches spelling misses (ADR 0020). It adds candidates; it never removes one the rules found."""
    if not practice_ai.enabled() or not parties or not known:
        return []
    found = {(h["party"], h["matched"], h["source"]) for h in hits}
    pool = [k for k in known if not any((x.name, k.name, k.source) in found for x in parties)]
    pool.sort(key=lambda k: -max(similarity(x.name, k.name) for x in parties))
    pool = pool[:SECOND_LOOK_NAMES]
    got = practice_ai.conflict_second_look([(x.name, x.role) for x in parties], [(k.name, k.role, k.source) for k in pool],
                                           set(), practice_ai.factory_of(s), tenant_id)
    out = []
    for m in got or []:
        x, k = parties[m["party"]], pool[m["known"]]
        out.append({"party": x.name, "party_role": x.role, "matched": k.name, "matched_role": k.role,
                    "source": k.source, "score": round(similarity(x.name, k.name), 3), "kind": _kind(x.role, k.role),
                    "reason": f"Claude: {m['reason']}", "found_by": "claude"})
    return out


def run_conflict_check(s: Session, p: Principal, matter: MatterRow) -> ConflictCheckRow:
    """Compare this matter's parties with every other matter's parties and the firm's screening lists."""
    parties = [Party(x.name, x.role, x.identifier or "") for x in s.scalars(select(MatterPartyRow).where(
        MatterPartyRow.tenant_id == p.tenant_id, MatterPartyRow.matter_id == matter.id))]
    others = s.execute(select(MatterPartyRow, MatterRow.reference).join(MatterRow, MatterRow.id == MatterPartyRow.matter_id)
                       .where(MatterPartyRow.tenant_id == p.tenant_id, MatterPartyRow.matter_id != matter.id)).all()
    known = [Known(x.name, x.role, ref, x.identifier or "") for x, ref in others]
    known += [Known(w.name, "watchlist", w.list_name + (f" ({w.reference})" if w.reference else ""), w.identifier or "")
              for w in s.scalars(select(WatchlistEntryRow).where(WatchlistEntryRow.tenant_id == p.tenant_id))]
    hits = [{**asdict(h), "found_by": "rules"} for h in check(parties, known)]
    hits += _second_look(s, p.tenant_id, parties, known, hits)
    row = ConflictCheckRow(tenant_id=p.tenant_id, matter_id=matter.id, hits=json.dumps(hits),
                           compared=len(known), status="pending", run_by=p.user)
    s.add(row)
    _event(s, matter, "conflict_check", p.user, f"{len(hits)} possible match{'es' if len(hits) != 1 else ''} "
                                                 f"against {len(known)} names")
    return row


def open_matter(s: Session, p: Principal, title: str, matter_type: str, client_kind: str,
                parties: list[tuple[str, str, str]], responsible: str | None) -> MatterRow:
    require(p, *ANY)
    title = " ".join(title.split())
    if not 3 <= len(title) <= 200:
        raise Invalid("give the matter a title of 3 to 200 characters")
    cleaned = [(" ".join(n.split())[:200], r, (i or "").strip()[:64]) for n, r, i in parties if n.strip()]
    if not any(r == "client" for _, r, _ in cleaned):
        raise Invalid("name at least one client")
    if len(cleaned) > MAX_PARTIES or any(r not in ROLES for _, r, _ in cleaned):
        raise Invalid(f"list up to {MAX_PARTIES} parties, each a client, counterparty, related party or beneficial owner")
    matter = MatterRow(tenant_id=p.tenant_id, reference=_next_reference(s, p.tenant_id), title=title,
                       matter_type=matter_type[:40] or "general", client_kind=client_kind, status="conflict_review",
                       responsible=responsible or p.user, created_by=p.user)
    s.add(matter)
    s.flush()
    for name, role, identifier in cleaned:
        s.add(MatterPartyRow(tenant_id=p.tenant_id, matter_id=matter.id, name=name, role=role, identifier=identifier or None))
    s.flush()
    _event(s, matter, "opened", p.user)
    run_conflict_check(s, p, matter)
    s.commit()
    return matter


def latest_check(s: Session, tenant_id: str, matter_id: str) -> ConflictCheckRow | None:
    return s.scalar(select(ConflictCheckRow).where(ConflictCheckRow.tenant_id == tenant_id,
                                                   ConflictCheckRow.matter_id == matter_id)
                    .order_by(ConflictCheckRow.run_at.desc(), ConflictCheckRow.id).limit(1))


def decide_conflict(s: Session, p: Principal, matter_id: str, decision: str, note: str) -> MatterRow:
    """Only a reviewer or partner who did not open the matter decides, and with possible matches they must say why."""
    require(p, REVIEWER, PARTNER)
    matter = get_matter(s, p.tenant_id, matter_id)
    if matter.status != "conflict_review":
        raise Conflict("this matter's conflict check has already been decided")
    if p.user == matter.created_by:
        raise Forbidden("maker-checker: someone other than the person who opened the matter must decide")
    row = latest_check(s, p.tenant_id, matter.id)
    note = " ".join(note.split())
    if decision == "conflict" or (row and json.loads(row.hits)):
        if len(note) < 5:
            raise Invalid("record the reasons for this decision")
    row.status, row.decided_by, row.decision_note, row.decided_at = decision, p.user, note[:1000] or None, datetime.now(UTC)
    matter.status = "open" if decision == "cleared" else "declined"
    _event(s, matter, f"conflict_{decision}", p.user, note[:1000] or None)
    s.commit()
    return matter


def add_requirement(s: Session, store: BlobStore, p: Principal, matter_id: str, item: str, filename: str, data: bytes,
                    expires_on: date | None) -> MatterRequirementRow:
    require(p, *ANY)
    matter = get_matter(s, p.tenant_id, matter_id)
    if item not in kyc.ITEMS:
        raise Invalid(f"item must be one of {', '.join(kyc.ITEMS)}")
    media_type = _allowed(filename, data)
    if media_type is None:
        raise Invalid("attach a PDF, image, Excel or CSV file whose contents match its name, up to 25 MB")
    sha = hashlib.sha256(data).hexdigest()
    store.put(sha, data)
    row = MatterRequirementRow(tenant_id=p.tenant_id, matter_id=matter.id, item=item, status="provided",
                               filename=filename[:255], sha256=sha, content_type=media_type, expires_on=expires_on,
                               provided_by=p.user)
    s.add(row)
    _event(s, matter, "kyc_provided", p.user, kyc.ITEMS[item])
    s.commit()
    return row


def verify_requirement(s: Session, p: Principal, matter_id: str, requirement_id: str) -> MatterRequirementRow:
    require(p, REVIEWER, PARTNER)
    matter = get_matter(s, p.tenant_id, matter_id)
    row = s.scalar(select(MatterRequirementRow).where(MatterRequirementRow.tenant_id == p.tenant_id,
                                                      MatterRequirementRow.matter_id == matter.id,
                                                      MatterRequirementRow.id == requirement_id))
    if row is None:
        raise NotFound("file item not found")
    if row.status == "verified":
        raise Conflict("already verified")
    if p.user == row.provided_by:
        raise Forbidden("maker-checker: someone other than the person who provided it must verify it")
    row.status, row.verified_by, row.verified_at = "verified", p.user, datetime.now(UTC)
    _event(s, matter, "kyc_verified", p.user, kyc.ITEMS[row.item])
    s.commit()
    return row


def _items(s: Session, tenant_id: str, matter_id: str) -> list[MatterRequirementRow]:
    return list(s.scalars(select(MatterRequirementRow).where(MatterRequirementRow.tenant_id == tenant_id,
                                                             MatterRequirementRow.matter_id == matter_id)
                          .order_by(MatterRequirementRow.provided_at)))


def matter_gaps(s: Session, matter: MatterRow, today: date) -> list[kyc.Gap]:
    """The newest copy of each item counts, so replacing an expired ID clears the gap once verified."""
    holds_money = bool(s.scalar(select(func.count()).select_from(TrustEntryRow).where(
        TrustEntryRow.tenant_id == matter.tenant_id, TrustEntryRow.matter_id == matter.id)))
    latest: dict[str, MatterRequirementRow] = {}
    for row in _items(s, matter.tenant_id, matter.id):
        latest[row.item] = row
    items = [kyc.Item(r.item, r.status, r.expires_on) for r in latest.values()]
    return kyc.gaps(kyc.required(matter.client_kind, holds_money), items, today)


class PartyIn(BaseModel):
    name: str = Field(max_length=200)
    role: Literal["client", "counterparty", "related", "beneficial_owner"]
    identifier: str = Field(default="", max_length=64)


class MatterIn(BaseModel):
    title: str = Field(max_length=200)
    matter_type: str = Field(default="general", max_length=40)
    client_kind: Literal["person", "organisation"]
    parties: list[PartyIn] = Field(min_length=1, max_length=MAX_PARTIES)
    responsible: str | None = Field(default=None, max_length=200)


class DecisionIn(BaseModel):
    decision: Literal["cleared", "conflict"]
    note: str = Field(default="", max_length=1000)


class GapOut(BaseModel):
    item: str
    label: str
    problem: str
    detail: str


class RequirementOut(BaseModel):
    id: str
    item: str
    label: str
    status: str
    filename: str
    expires_on: date | None
    provided_by: str
    verified_by: str | None

    @classmethod
    def of(cls, r: MatterRequirementRow) -> "RequirementOut":
        return cls(id=r.id, item=r.item, label=kyc.ITEMS[r.item], status=r.status, filename=r.filename,
                   expires_on=r.expires_on, provided_by=r.provided_by, verified_by=r.verified_by)


class MatterSummaryOut(BaseModel):
    id: str
    reference: str
    title: str
    matter_type: str
    client_kind: str
    status: str
    responsible: str | None
    created_by: str
    created_at: datetime
    possible_conflicts: int
    gaps: int


class MatterOut(MatterSummaryOut):
    parties: list[PartyIn]
    conflict: dict | None
    requirements: list[RequirementOut]
    file_gaps: list[GapOut]
    events: list[dict]


def _summary(s: Session, m: MatterRow, today: date) -> MatterSummaryOut:
    check_row = latest_check(s, m.tenant_id, m.id)
    return MatterSummaryOut(id=m.id, reference=m.reference, title=m.title, matter_type=m.matter_type,
                            client_kind=m.client_kind, status=m.status, responsible=m.responsible,
                            created_by=m.created_by, created_at=m.created_at,
                            possible_conflicts=len(json.loads(check_row.hits)) if check_row else 0,
                            gaps=len(matter_gaps(s, m, today)) if m.status == "open" else 0)


def _full(s: Session, m: MatterRow, today: date) -> MatterOut:
    check_row = latest_check(s, m.tenant_id, m.id)
    parties = s.scalars(select(MatterPartyRow).where(MatterPartyRow.tenant_id == m.tenant_id,
                                                     MatterPartyRow.matter_id == m.id))
    events = s.scalars(select(MatterEventRow).where(MatterEventRow.tenant_id == m.tenant_id,
                                                    MatterEventRow.matter_id == m.id).order_by(MatterEventRow.at))
    return MatterOut(
        **_summary(s, m, today).model_dump(),
        parties=[PartyIn(name=x.name, role=x.role, identifier=x.identifier or "") for x in parties],
        conflict=None if check_row is None else {
            "status": check_row.status, "compared": check_row.compared, "hits": json.loads(check_row.hits),
            "run_by": check_row.run_by, "run_at": check_row.run_at.isoformat(), "decided_by": check_row.decided_by,
            "decision_note": check_row.decision_note},
        requirements=[RequirementOut.of(r) for r in _items(s, m.tenant_id, m.id)],
        file_gaps=[GapOut(**asdict(g)) for g in matter_gaps(s, m, today)],
        events=[{"action": e.action, "actor": e.actor, "note": e.note, "at": e.at.isoformat()} for e in events],
    )


class CertificateIn(BaseModel):
    holder: str = Field(max_length=320)
    number: str = Field(max_length=64)
    expires_on: date


def add_routes(app: FastAPI, Auth, Db, store: BlobStore) -> None:  # noqa: N803 - FastAPI dependency aliases
    @app.get("/matters", response_model=list[MatterSummaryOut])
    def list_matters(p: Auth, s: Db) -> list[MatterSummaryOut]:
        today = date.today()
        rows = s.scalars(select(MatterRow).where(MatterRow.tenant_id == p.tenant_id).order_by(MatterRow.created_at.desc()))
        return [_summary(s, m, today) for m in rows]

    @app.post("/matters", response_model=MatterOut, status_code=201)
    def post_matter(body: MatterIn, p: Auth, s: Db) -> MatterOut:
        m = open_matter(s, p, body.title, body.matter_type, body.client_kind,
                        [(x.name, x.role, x.identifier) for x in body.parties], body.responsible)
        return _full(s, m, date.today())

    @app.get("/matters/{matter_id}", response_model=MatterOut)
    def get_one(matter_id: str, p: Auth, s: Db) -> MatterOut:
        return _full(s, get_matter(s, p.tenant_id, matter_id), date.today())

    @app.post("/matters/{matter_id}/conflict-check", response_model=MatterOut)
    def rerun(matter_id: str, p: Auth, s: Db) -> MatterOut:
        require(p, *ANY)
        m = get_matter(s, p.tenant_id, matter_id)
        if m.status != "conflict_review":
            raise Conflict("the conflict check has already been decided")
        run_conflict_check(s, p, m)
        s.commit()
        return _full(s, m, date.today())

    @app.post("/matters/{matter_id}/conflict-decision", response_model=MatterOut)
    def decide(matter_id: str, body: DecisionIn, p: Auth, s: Db) -> MatterOut:
        return _full(s, decide_conflict(s, p, matter_id, body.decision, body.note), date.today())

    @app.post("/matters/{matter_id}/requirements", response_model=RequirementOut, status_code=201)
    def upload_requirement(matter_id: str, p: Auth, s: Db, file: Annotated[UploadFile, File()],
                           item: Annotated[str, Form(max_length=32)],
                           expires_on: Annotated[date | None, Form()] = None) -> RequirementOut:
        data = file.file.read(MAX_UPLOAD_BYTES + 1)
        return RequirementOut.of(add_requirement(s, store, p, matter_id, item, file.filename or "", data, expires_on))

    @app.post("/matters/{matter_id}/requirements/{requirement_id}/verify", response_model=RequirementOut)
    def verify(matter_id: str, requirement_id: str, p: Auth, s: Db) -> RequirementOut:
        return RequirementOut.of(verify_requirement(s, p, matter_id, requirement_id))

    @app.get("/matters/{matter_id}/requirements/{requirement_id}/file")
    def requirement_file(matter_id: str, requirement_id: str, p: Auth, s: Db) -> Response:
        row = s.scalar(select(MatterRequirementRow).where(MatterRequirementRow.tenant_id == p.tenant_id,
                                                          MatterRequirementRow.matter_id == matter_id,
                                                          MatterRequirementRow.id == requirement_id))
        if row is None:
            raise NotFound("file item not found")
        safe = re.sub(r"[^A-Za-z0-9._ -]", "_", row.filename) or "file"
        return Response(store.get(row.sha256), media_type=row.content_type,
                        headers={"Content-Disposition": f'attachment; filename="{safe}"'})

    @app.get("/practice/audit")
    def audit(p: Auth, s: Db) -> dict:
        """Every open matter's file gaps and every advocate's certificate, computed now (ADR 0018)."""
        today = date.today()
        matters = []
        for m in s.scalars(select(MatterRow).where(MatterRow.tenant_id == p.tenant_id, MatterRow.status == "open")
                           .order_by(MatterRow.reference)):
            found = matter_gaps(s, m, today)
            if found:
                matters.append({"id": m.id, "reference": m.reference, "title": m.title, "gaps": [asdict(g) for g in found]})
        advocates = [m.email for m in s.scalars(select(MemberRow).where(MemberRow.tenant_id == p.tenant_id,
                                                                        MemberRow.status == "active"))
                     if {"reviewer", "partner"} & set(m.roles.split(","))]
        certs = [kyc.Certificate(c.holder.lower(), c.expires_on) for c in s.scalars(
            select(PractisingCertificateRow).where(PractisingCertificateRow.tenant_id == p.tenant_id))]
        return {"as_of": today.isoformat(), "requirements_version": kyc.REQUIREMENTS_VERSION, "matters": matters,
                "certificates": [asdict(g) for g in kyc.certificate_gaps([a.lower() for a in advocates], certs, today)]}

    @app.post("/matters/{matter_id}/kyc-request")
    def kyc_request(matter_id: str, p: Auth, s: Db) -> dict:
        """Claude drafts the letter asking the client for what the file audit found missing (ADR 0020). It is a draft
        for the lawyer to edit and send; Provenant does not send it."""
        require(p, *ANY)
        if not practice_ai.enabled():
            raise Conflict("Claude is off: turn it on for the AI agents to draft the request")
        m = get_matter(s, p.tenant_id, matter_id)
        missing = [g.label for g in matter_gaps(s, m, date.today())]
        if not missing:
            raise Conflict("the file has nothing missing")
        client = " and ".join(x.name for x in s.scalars(select(MatterPartyRow).where(
            MatterPartyRow.tenant_id == p.tenant_id, MatterPartyRow.matter_id == m.id, MatterPartyRow.role == "client")))
        letter = practice_ai.kyc_request(client or "the client", f"{m.reference} ({m.title})", missing,
                                         practice_ai.factory_of(s), p.tenant_id)
        if letter is None:
            raise Conflict("Claude could not draft the request; try again")
        return {"letter": letter, "asks_for": missing}

    @app.get("/practice/certificates")
    def certificates(p: Auth, s: Db) -> list[dict]:
        return [{"holder": c.holder, "number": c.number, "expires_on": c.expires_on.isoformat()} for c in s.scalars(
            select(PractisingCertificateRow).where(PractisingCertificateRow.tenant_id == p.tenant_id)
            .order_by(PractisingCertificateRow.holder, PractisingCertificateRow.expires_on))]

    @app.post("/practice/certificates", status_code=201)
    def add_certificate(body: CertificateIn, p: Auth, s: Db) -> dict:
        require(p, PARTNER)
        s.add(PractisingCertificateRow(tenant_id=p.tenant_id, holder=body.holder.strip().lower(),
                                       number=body.number.strip(), expires_on=body.expires_on, recorded_by=p.user))
        s.commit()
        return {"holder": body.holder.strip().lower(), "expires_on": body.expires_on.isoformat()}

    @app.get("/practice/watchlists")
    def watchlists(p: Auth, s: Db) -> list[dict]:
        rows = s.execute(select(WatchlistEntryRow.list_name, func.count(), func.max(WatchlistEntryRow.uploaded_at))
                         .where(WatchlistEntryRow.tenant_id == p.tenant_id).group_by(WatchlistEntryRow.list_name)).all()
        return [{"list_name": n, "entries": c, "uploaded_at": u.isoformat()} for n, c, u in rows]

    @app.post("/practice/watchlists", status_code=201)
    def upload_watchlist(p: Auth, s: Db, file: Annotated[UploadFile, File()],
                         list_name: Annotated[str, Form(min_length=2, max_length=120)]) -> dict:
        """A partner uploads a screening list as CSV (name, and optionally identifier and reference). It replaces the
        list of the same name, because sanctions and PEP lists are republished whole."""
        require(p, PARTNER)
        data = file.file.read(MAX_UPLOAD_BYTES + 1)
        try:
            reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
        except UnicodeDecodeError as exc:
            raise Invalid("the list must be a UTF-8 CSV file") from exc
        fields = {f.strip().lower(): f for f in reader.fieldnames or []}
        if "name" not in fields:
            raise Invalid("the CSV needs a name column (and optionally identifier and reference)")
        rows = []
        for raw in reader:
            name = " ".join((raw.get(fields["name"]) or "").split())
            if name:
                pick = lambda key: (raw.get(fields[key]) or "").strip() if key in fields else ""  # noqa: E731
                rows.append(WatchlistEntryRow(tenant_id=p.tenant_id, list_name=list_name.strip(), name=name[:300],
                                              identifier=pick("identifier")[:64] or None,
                                              reference=pick("reference")[:120] or None, uploaded_by=p.user))
            if len(rows) > MAX_WATCHLIST:
                raise Invalid(f"a list can hold up to {MAX_WATCHLIST} names")
        if not rows:
            raise Invalid("the list has no names")
        for old in s.scalars(select(WatchlistEntryRow).where(WatchlistEntryRow.tenant_id == p.tenant_id,
                                                             WatchlistEntryRow.list_name == list_name.strip())):
            s.delete(old)
        s.add_all(rows)
        s.commit()
        return {"list_name": list_name.strip(), "entries": len(rows)}
