"""Docketing: reading court notices and computing deadlines from the firm's own rules (ADR 0019). Pure.

Limitation and filing periods are law that changes and differs by court, so each rule is firm data with a stated
basis. This module counts days. The notice reader only proposes what it finds; a person confirms the event before any
deadline is created.
"""
import calendar
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta

EVENTS = ("judgment", "ruling", "hearing", "mention", "service", "filing", "decree", "order")


@dataclass(frozen=True)
class Rule:
    name: str
    trigger: str  # the event kind that starts it, e.g. judgment
    days: int = 0
    months: int = 0
    counting: str = "calendar"  # calendar | court_days (weekends and holidays not counted)
    roll_forward: bool = True  # a deadline on a weekend or holiday moves to the next working day


def _working(day: date, holidays: set[date]) -> bool:
    return day.weekday() < 5 and day not in holidays


def _add_months(day: date, months: int) -> date:
    month = day.month - 1 + months
    year, month = day.year + month // 12, month % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def due_date(trigger: date, rule: Rule, holidays: Iterable[date] = ()) -> date:
    """Count from the day after the trigger. Months land on the same day of the month, or the month's last day."""
    off = set(holidays)
    if rule.counting == "court_days":
        day, left = trigger, rule.days
        while left > 0:
            day += timedelta(days=1)
            if _working(day, off):
                left -= 1
    else:
        day = _add_months(trigger, rule.months) + timedelta(days=rule.days)
    if rule.roll_forward:
        while not _working(day, off):
            day += timedelta(days=1)
    return day


_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m} | {
    m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
_DATE_TEXT = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:day\s+of\s+)?([A-Za-z]{3,9}),?\s+(\d{4})\b")
_DATE_NUM = re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\b")
_CASE = re.compile(
    r"\b((?:[A-Z]{2,6}\.?\s*)?(?:CIVIL\s+|CRIMINAL\s+|MISC\.?\s+|COMMERCIAL\s+)?(?:CASE|SUIT|APPEAL|PETITION|CAUSE|"
    r"APPLICATION|HCCC|HCCOMM|ELRC|ELC|CMCC)\s+(?:NO\.?|NUMBER)?\s*[A-Z]?\d{1,6}\s+OF\s+\d{4})", re.IGNORECASE)


@dataclass(frozen=True)
class Reading:
    """What the reader found in a notice. Every value is a proposal for a person to confirm or correct."""
    case_number: str | None
    event: str | None
    dates: list[date] = field(default_factory=list)
    evidence: dict[str, str] = field(default_factory=dict)  # value -> the words it came from


def read_notice(text: str) -> Reading:
    evidence = {}
    found = []
    for m in _DATE_TEXT.finditer(text):
        month = _MONTHS.get(m[2].lower())
        if month:
            try:
                d = date(int(m[3]), month, int(m[1]))
            except ValueError:
                continue
            found.append((m.start(), d, m[0]))
    for m in _DATE_NUM.finditer(text):
        try:
            d = date(int(m[3]), int(m[2]), int(m[1]))  # Kenyan notices write day first
        except ValueError:
            continue
        found.append((m.start(), d, m[0]))
    dates = []
    for _, d, words in sorted(found):
        if d not in dates:
            dates.append(d)
            evidence[d.isoformat()] = words
    case = _CASE.search(text)
    if case:
        evidence["case_number"] = case[0]
    lowered = text.lower()
    hits = sorted((lowered.find(e), e) for e in EVENTS if e in lowered)
    event = hits[0][1] if hits else None
    if event:
        start = lowered.find(event)
        evidence["event"] = text[max(start - 40, 0):start + len(event) + 40].strip()
    return Reading(" ".join(case[0].split()).upper() if case else None, event, dates, evidence)
