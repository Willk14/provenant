"""Docketing: court notices, the firm's deadline rules and holidays, and each matter's deadlines (ADR 0019)."""
import json
from datetime import UTC, date, datetime

from fastapi import FastAPI, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from provenant.api.auth import PARTNER, PREPARER, REVIEWER
from provenant.api.db import CourtHolidayRow, CourtNoticeRow, DeadlineRuleRow, DocketEntryRow, MatterRow
from provenant.api.matters import _event, get_matter
from provenant.api.notifications import notify
from provenant.api.services import Conflict, Invalid, NotFound, require
from provenant.practice import deadlines as engine

ANY = (PREPARER, REVIEWER, PARTNER)
MAX_NOTICE = 50_000


def _holidays(s: Session, tenant_id: str) -> list[date]:
    return [h.day for h in s.scalars(select(CourtHolidayRow).where(CourtHolidayRow.tenant_id == tenant_id))]


def _rule(row: DeadlineRuleRow) -> engine.Rule:
    return engine.Rule(row.name, row.trigger, row.days, row.months, row.counting, row.roll_forward)


def _describe(row: DeadlineRuleRow) -> str:
    span = " and ".join(x for x in (f"{row.months} month{'s' if row.months != 1 else ''}" if row.months else "",
                                    f"{row.days} {'court ' if row.counting == 'court_days' else ''}day{'s' if row.days != 1 else ''}"
                                    if row.days else "") if x)
    return f"{span or 'same day'} after the {row.trigger}{', next working day if it falls on a weekend or holiday' if row.roll_forward else ''}"


class RuleIn(BaseModel):
    name: str = Field(min_length=3, max_length=160)
    trigger: str = Field(min_length=3, max_length=32)
    days: int = Field(default=0, ge=0, le=3650)
    months: int = Field(default=0, ge=0, le=240)
    counting: str = Field(default="calendar", pattern="^(calendar|court_days)$")
    roll_forward: bool = True
    basis: str = Field(min_length=5, max_length=300)


class HolidayIn(BaseModel):
    day: date
    name: str = Field(min_length=2, max_length=120)


class NoticeIn(BaseModel):
    text: str = Field(min_length=20, max_length=MAX_NOTICE)


class ConfirmIn(BaseModel):
    event: str = Field(min_length=3, max_length=32)
    event_date: date
    case_number: str = Field(default="", max_length=80)
    rule_ids: list[str] | None = Field(default=None, max_length=40)


class EntryIn(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    due_on: date
    basis: str = Field(min_length=3, max_length=400)


def _entry_out(row: DocketEntryRow, reference: str = "") -> dict:
    return {"id": row.id, "matter_id": row.matter_id, "matter": reference, "title": row.title,
            "due_on": row.due_on.isoformat(), "basis": row.basis, "status": row.status, "done_by": row.done_by}


def _notice_out(row: CourtNoticeRow) -> dict:
    return {"id": row.id, "status": row.status, "text": row.text, "reading": json.loads(row.reading),
            "event": row.event, "event_date": row.event_date.isoformat() if row.event_date else None,
            "case_number": row.case_number, "created_by": row.created_by, "confirmed_by": row.confirmed_by}


def add_routes(app: FastAPI, Auth, Db) -> None:  # noqa: N803 - FastAPI dependency aliases
    @app.get("/docket/rules")
    def rules(p: Auth, s: Db) -> list[dict]:
        rows = s.scalars(select(DeadlineRuleRow).where(DeadlineRuleRow.tenant_id == p.tenant_id,
                                                       DeadlineRuleRow.active.is_(True)).order_by(DeadlineRuleRow.trigger))
        return [{"id": r.id, "name": r.name, "trigger": r.trigger, "days": r.days, "months": r.months,
                 "counting": r.counting, "roll_forward": r.roll_forward, "basis": r.basis, "describe": _describe(r)}
                for r in rows]

    @app.post("/docket/rules", status_code=201)
    def add_rule(body: RuleIn, p: Auth, s: Db) -> dict:
        """A partner enters each period with its basis (the statute, rule or practice direction). Provenant does not
        hold limitation law itself, because it changes and differs by court."""
        require(p, PARTNER)
        if not (body.days or body.months):
            raise Invalid("a rule needs a number of days or months")
        row = DeadlineRuleRow(tenant_id=p.tenant_id, name=body.name.strip(), trigger=body.trigger.strip().lower(),
                              days=body.days, months=body.months, counting=body.counting,
                              roll_forward=body.roll_forward, basis=body.basis.strip(), active=True, created_by=p.user)
        s.add(row)
        s.commit()
        return {"id": row.id, "describe": _describe(row)}

    @app.post("/docket/rules/{rule_id}/retire")
    def retire(rule_id: str, p: Auth, s: Db) -> dict:
        require(p, PARTNER)
        row = s.scalar(select(DeadlineRuleRow).where(DeadlineRuleRow.tenant_id == p.tenant_id, DeadlineRuleRow.id == rule_id))
        if row is None:
            raise NotFound("rule not found")
        row.active = False
        s.commit()
        return {"id": row.id, "active": False}

    @app.get("/docket/holidays")
    def holidays(p: Auth, s: Db) -> list[dict]:
        rows = s.scalars(select(CourtHolidayRow).where(CourtHolidayRow.tenant_id == p.tenant_id).order_by(CourtHolidayRow.day))
        return [{"id": h.id, "day": h.day.isoformat(), "name": h.name} for h in rows]

    @app.post("/docket/holidays", status_code=201)
    def add_holiday(body: HolidayIn, p: Auth, s: Db) -> dict:
        require(p, PARTNER)
        if s.scalar(select(CourtHolidayRow).where(CourtHolidayRow.tenant_id == p.tenant_id, CourtHolidayRow.day == body.day)):
            raise Conflict("that day is already a holiday")
        s.add(CourtHolidayRow(tenant_id=p.tenant_id, day=body.day, name=body.name.strip()))
        s.commit()
        return {"day": body.day.isoformat(), "name": body.name.strip()}

    @app.post("/matters/{matter_id}/notices", status_code=201)
    def add_notice(matter_id: str, body: NoticeIn, p: Auth, s: Db) -> dict:
        """The reader proposes a case number, event and dates, with the words each came from. Nothing is docketed
        until a person confirms the event."""
        require(p, *ANY)
        matter = get_matter(s, p.tenant_id, matter_id)
        reading = engine.read_notice(body.text)
        row = CourtNoticeRow(tenant_id=p.tenant_id, matter_id=matter.id, text=body.text, status="new",
                             reading=json.dumps({"case_number": reading.case_number, "event": reading.event,
                                                 "dates": [d.isoformat() for d in reading.dates],
                                                 "evidence": reading.evidence}), created_by=p.user)
        s.add(row)
        _event(s, matter, "notice_received", p.user)
        s.commit()
        return _notice_out(row)

    @app.get("/matters/{matter_id}/docket")
    def matter_docket(matter_id: str, p: Auth, s: Db) -> dict:
        matter = get_matter(s, p.tenant_id, matter_id)
        notices = s.scalars(select(CourtNoticeRow).where(CourtNoticeRow.tenant_id == p.tenant_id,
                                                         CourtNoticeRow.matter_id == matter.id)
                            .order_by(CourtNoticeRow.created_at.desc()))
        entries = s.scalars(select(DocketEntryRow).where(DocketEntryRow.tenant_id == p.tenant_id,
                                                         DocketEntryRow.matter_id == matter.id).order_by(DocketEntryRow.due_on))
        return {"notices": [_notice_out(n) for n in notices], "entries": [_entry_out(e, matter.reference) for e in entries]}

    @app.post("/matters/{matter_id}/notices/{notice_id}/confirm")
    def confirm(matter_id: str, notice_id: str, body: ConfirmIn, p: Auth, s: Db) -> dict:
        require(p, *ANY)
        matter = get_matter(s, p.tenant_id, matter_id)
        notice = s.scalar(select(CourtNoticeRow).where(CourtNoticeRow.tenant_id == p.tenant_id,
                                                       CourtNoticeRow.matter_id == matter.id, CourtNoticeRow.id == notice_id))
        if notice is None:
            raise NotFound("notice not found")
        if notice.status != "new":
            raise Conflict(f"this notice was already {notice.status}")
        event = body.event.strip().lower()
        query = select(DeadlineRuleRow).where(DeadlineRuleRow.tenant_id == p.tenant_id, DeadlineRuleRow.active.is_(True))
        chosen = [r for r in s.scalars(query) if (r.id in body.rule_ids if body.rule_ids is not None else r.trigger == event)]
        off = _holidays(s, p.tenant_id)
        made = []
        for rule in chosen:
            due = engine.due_date(body.event_date, _rule(rule), off)
            entry = DocketEntryRow(tenant_id=p.tenant_id, matter_id=matter.id, notice_id=notice.id, rule_id=rule.id,
                                   title=rule.name, due_on=due, status="open", created_by=p.user,
                                   basis=f"{_describe(rule)} on {body.event_date.isoformat()}. Basis: {rule.basis}"[:400])
            s.add(entry)
            made.append(entry)
        notice.status, notice.event, notice.event_date = "confirmed", event, body.event_date
        notice.case_number, notice.confirmed_by = body.case_number.strip()[:80] or None, p.user
        _event(s, matter, "notice_confirmed", p.user, f"{event} on {body.event_date.isoformat()}: {len(made)} deadline(s)")
        if made and matter.responsible:
            notify(s, p.tenant_id, "deadline_set", "New deadlines on one of your matters",
                   f"{len(made)} new deadline{'s were' if len(made) != 1 else ' was'} added from a court notice.",
                   f"/matters/{matter.id}", people=(matter.responsible,), exclude=(p.user,))
        s.commit()
        return {"notice": _notice_out(notice), "entries": [_entry_out(e, matter.reference) for e in made]}

    @app.post("/matters/{matter_id}/docket", status_code=201)
    def add_entry(matter_id: str, body: EntryIn, p: Auth, s: Db) -> dict:
        require(p, *ANY)
        matter = get_matter(s, p.tenant_id, matter_id)
        row = DocketEntryRow(tenant_id=p.tenant_id, matter_id=matter.id, title=body.title.strip(), due_on=body.due_on,
                             basis=body.basis.strip(), status="open", created_by=p.user)
        s.add(row)
        _event(s, matter, "deadline_added", p.user, f"{row.title} on {row.due_on.isoformat()}")
        s.commit()
        return _entry_out(row, matter.reference)

    @app.post("/docket/{entry_id}/done")
    def done(entry_id: str, p: Auth, s: Db) -> dict:
        require(p, *ANY)
        row = s.scalar(select(DocketEntryRow).where(DocketEntryRow.tenant_id == p.tenant_id, DocketEntryRow.id == entry_id))
        if row is None:
            raise NotFound("deadline not found")
        row.status, row.done_by, row.done_at = "done", p.user, datetime.now(UTC)
        s.commit()
        return _entry_out(row)

    def _open(s: Session, tenant_id: str) -> list[tuple[DocketEntryRow, str]]:
        return list(s.execute(select(DocketEntryRow, MatterRow.reference).join(MatterRow, MatterRow.id == DocketEntryRow.matter_id)
                              .where(DocketEntryRow.tenant_id == tenant_id, DocketEntryRow.status == "open")
                              .order_by(DocketEntryRow.due_on)).all())

    @app.get("/docket")
    def docket(p: Auth, s: Db) -> list[dict]:
        return [_entry_out(e, ref) for e, ref in _open(s, p.tenant_id)]

    @app.get("/docket.ics")
    def calendar(p: Auth, s: Db) -> Response:
        """Open deadlines as an all-day calendar feed, to import into Outlook or Google Calendar."""
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        clean = lambda t: t.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", " ")  # noqa: E731
        lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Provenant//Docket//EN"]
        for e, ref in _open(s, p.tenant_id):
            day = e.due_on.strftime("%Y%m%d")
            lines += ["BEGIN:VEVENT", f"UID:{e.id}@provenant", f"DTSTAMP:{stamp}", f"DTSTART;VALUE=DATE:{day}",
                      f"SUMMARY:{clean(f'{ref}: {e.title}')}", f"DESCRIPTION:{clean(e.basis)}", "END:VEVENT"]
        lines.append("END:VCALENDAR")
        return Response("\r\n".join(lines) + "\r\n", media_type="text/calendar",
                        headers={"Content-Disposition": 'attachment; filename="provenant-docket.ics"'})
