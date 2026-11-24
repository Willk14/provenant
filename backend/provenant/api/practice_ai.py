"""Claude for the practice agents (ADR 0020).

Each agent hands Claude the material and one `submit` tool. The tool checks every answer against the source before it
is accepted: quotes must appear word for word, ids must exist, and dates and amounts are worked out here, never taken
from the model. A rejected answer goes back to Claude with the reason, so it can correct itself. Claude only proposes;
the rules engines still count the days and the money, and a person still decides. When Claude is off, or a run fails,
each agent falls back to its rules-based result, and says so.
"""
import logging
from weakref import WeakKeyDictionary
from collections.abc import Callable, Mapping, Sequence
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from provenant.ai import Limits, run_tools
from provenant.ai.harness import MODEL
from provenant.api import ai_runs
from provenant.api.db import make_sessionmaker

log = logging.getLogger(__name__)

LIMITS = Limits(deadline_seconds=150, idle_seconds=60, max_turns=8, max_cost_microusd=1_000_000, max_output_tokens=8_000)
MAX_TEXT = 60_000  # characters of source material sent in one run
NOTICE_EVENTS = ("judgment", "ruling", "hearing", "mention", "service", "filing", "decree", "order", "other")

# Tests replace this with a scripted client.
client_factory: Callable[[], Any] | None = None


def enabled() -> bool:
    """Claude runs the practice agents whenever it runs the contract agents (the AI agents page's engine switch)."""
    from provenant.api.ai_agents import engine_status

    return client_factory is not None or engine_status().mode == "claude"


_factories: WeakKeyDictionary = WeakKeyDictionary()  # engine -> factory, dropped with the engine


def factory_of(s: Session) -> sessionmaker[Session]:
    """A session factory on the same database, so a run is recorded even if the request's own work rolls back. It is
    made by `make_sessionmaker`, so on Postgres each transaction sets the tenant that row-level security checks."""
    engine = s.get_bind()
    if engine not in _factories:
        _factories[engine] = make_sessionmaker(engine)
    return _factories[engine]


def _client() -> Any:
    if client_factory is not None:
        return client_factory()
    import anthropic

    return anthropic.Anthropic()


def _squash(text: str) -> str:
    return " ".join(text.split()).lower()


def quoted(quote: str, source: str) -> bool:
    """True when the quote appears in the source, ignoring case and runs of whitespace."""
    q = _squash(quote)
    return len(q) >= 3 and q in _squash(source)


class Submission:
    """One `submit` tool whose input is checked by `check`, which returns problems (rejected) or nothing (accepted)."""

    def __init__(self, check: Callable[[Mapping[str, Any]], list[str]]) -> None:
        self.check, self.result, self.finished = check, None, False

    def call(self, name: str, tool_input: Mapping[str, Any]) -> str:
        if name != "submit":
            return f"Unknown tool {name}. Use submit."
        problems = self.check(tool_input)
        if problems:
            return "Not accepted. Fix these and submit again:\n- " + "\n- ".join(problems[:20])
        self.result, self.finished = dict(tool_input), True
        return "Accepted."


SYSTEM = (
    "You work inside Provenant, a practice system for a Kenyan law firm. You assist advocates; you do not give legal "
    "advice and you never decide. Use only the material in the message. Quote words exactly as they appear. If the "
    "material does not say something, leave it empty rather than guessing. Finish by calling submit once; if it is "
    "not accepted, correct exactly what it lists and submit again."
)


def _run(factory: sessionmaker[Session] | None, tenant_id: str, purpose: str, prompt: str, schema: dict,
         description: str, check: Callable[[Mapping[str, Any]], list[str]]) -> dict | None:
    """Run Claude with one checked submit tool. Returns the accepted input, or None when the run did not finish."""
    box = Submission(check)
    tools = [{"name": "submit", "description": description, "input_schema": schema}]
    try:
        if factory is not None:
            with ai_runs.recording(factory, tenant_id, None):
                run_tools(_client(), purpose=purpose, system=SYSTEM, prompt=prompt, tools=tools, execute=box.call,
                          done=lambda: box.finished, model=MODEL, limits=LIMITS)
        else:
            run_tools(_client(), purpose=purpose, system=SYSTEM, prompt=prompt, tools=tools, execute=box.call,
                      done=lambda: box.finished, model=MODEL, limits=LIMITS)
    except Exception:
        log.exception("Claude %s run did not finish; the rules-based result is used", purpose)
        return None
    return box.result


def _iso(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


# ---- docketing: reading a court notice ----------------------------------------------------------------------------

NOTICE_SCHEMA = {
    "type": "object",
    "properties": {
        "case_number": {"type": "string", "description": "As written in the notice, or empty."},
        "event": {"type": "string", "enum": list(NOTICE_EVENTS),
                  "description": "The event that starts the time running, e.g. a judgment delivered."},
        "event_quote": {"type": "string", "description": "The exact words naming the event."},
        "event_date": {"type": "string", "description": "YYYY-MM-DD: the date the event happened, or empty."},
        "event_date_quote": {"type": "string", "description": "The exact words giving that date."},
        "other_dates": {"type": "array", "maxItems": 12, "items": {"type": "object", "properties": {
            "date": {"type": "string"}, "quote": {"type": "string"}, "what": {"type": "string"}},
            "required": ["date", "quote", "what"]}},
    },
    "required": ["case_number", "event", "event_quote", "event_date", "event_date_quote", "other_dates"],
}


def read_notice(text: str, factory: sessionmaker[Session] | None, tenant_id: str) -> dict | None:
    """Claude's reading in the shape the docket shows: case number, event, dates (event date first) and evidence."""

    def check(x: Mapping[str, Any]) -> list[str]:
        problems = []
        if x.get("case_number") and not quoted(x["case_number"], text):
            problems.append("case_number does not appear in the notice")
        if x.get("event") not in NOTICE_EVENTS:
            problems.append("event must be one of " + ", ".join(NOTICE_EVENTS))
        if x.get("event_quote") and not quoted(x["event_quote"], text):
            problems.append("event_quote is not in the notice word for word")
        if x.get("event_date"):
            if _iso(x["event_date"]) is None:
                problems.append("event_date must be YYYY-MM-DD")
            if not quoted(x.get("event_date_quote", ""), text):
                problems.append("event_date_quote is not in the notice word for word")
        for i, d in enumerate(x.get("other_dates") or []):
            if _iso(d.get("date", "")) is None:
                problems.append(f"other_dates[{i}].date must be YYYY-MM-DD")
            if not quoted(d.get("quote", ""), text):
                problems.append(f"other_dates[{i}].quote is not in the notice word for word")
        return problems

    got = _run(factory, tenant_id, "notice_reading",
               "Read this court notice or registry email and submit what it says.\n\n<notice>\n"
               f"{text[:MAX_TEXT]}\n</notice>", NOTICE_SCHEMA, "Submit the reading of the notice.", check)
    if got is None:
        return None
    dates, evidence = [], {}
    if got.get("event_date"):
        dates.append(got["event_date"])
        evidence[got["event_date"]] = got["event_date_quote"]
    for d in got.get("other_dates") or []:
        if d["date"] not in dates:
            dates.append(d["date"])
            evidence[d["date"]] = f'{d["quote"]} ({d["what"]})'
    if got.get("case_number"):
        evidence["case_number"] = got["case_number"]
    event = None if got["event"] == "other" else got["event"]
    if event:
        evidence["event"] = got["event_quote"]
    return {"case_number": " ".join(got["case_number"].split()).upper() or None, "event": event, "dates": dates,
            "evidence": evidence, "reader": "claude"}


# ---- conflict checking: a second look at names the rules did not match ---------------------------------------------

CONFLICT_SCHEMA = {
    "type": "object",
    "properties": {"matches": {"type": "array", "maxItems": 40, "items": {"type": "object", "properties": {
        "party": {"type": "integer", "description": "Number of the new party."},
        "known": {"type": "integer", "description": "Number of the known name."},
        "reason": {"type": "string", "description": "Why these may be the same person or organisation."}},
        "required": ["party", "known", "reason"]}}},
    "required": ["matches"],
}


def conflict_second_look(parties: Sequence[tuple[str, str]], known: Sequence[tuple[str, str, str]],
                         already: set[tuple[int, int]], factory: sessionmaker[Session] | None,
                         tenant_id: str) -> list[dict] | None:
    """Names that may be the same person or organisation although the rules scored them below the threshold:
    abbreviations, initials, transliterations, former names. Returns {party, known, reason} by index."""
    if not parties or not known:
        return []

    def check(x: Mapping[str, Any]) -> list[str]:
        problems = []
        for i, m in enumerate(x.get("matches") or []):
            if not (0 <= m.get("party", -1) < len(parties)) or not (0 <= m.get("known", -1) < len(known)):
                problems.append(f"matches[{i}] uses a number that is not in the lists")
            elif len(str(m.get("reason", "")).strip()) < 5:
                problems.append(f"matches[{i}] needs a reason")
        return problems

    lines = "\n".join(f"P{i}: {name} ({role})" for i, (name, role) in enumerate(parties))
    names = "\n".join(f"K{i}: {name} ({role}, {source})" for i, (name, role, source) in enumerate(known))[:MAX_TEXT]
    got = _run(factory, tenant_id, "conflict_second_look",
               "A conflict check compared these new parties with the firm's known names, using spelling similarity. "
               "List only pairs that may be the same person or organisation and that spelling alone would miss "
               "(abbreviations, initials, a group company, a former or translated name). Submit an empty list when "
               "there are none. Numbers only: use the number after P and K.\n\n"
               f"New parties:\n{lines}\n\nKnown names:\n{names}", CONFLICT_SCHEMA,
               "Submit the possible matches.", check)
    if got is None:
        return None
    seen, out = set(already), []
    for m in got["matches"]:
        key = (m["party"], m["known"])
        if key not in seen:
            seen.add(key)
            out.append({"party": m["party"], "known": m["known"], "reason": " ".join(m["reason"].split())[:300]})
    return out


# ---- precedent drafting: values for the fields nobody has filled ----------------------------------------------------

FIELDS_SCHEMA = {
    "type": "object",
    "properties": {"values": {"type": "array", "maxItems": 40, "items": {"type": "object", "properties": {
        "field": {"type": "string"}, "value": {"type": "string"},
        "document": {"type": "integer", "description": "Number of the document the value comes from."},
        "page": {"type": "integer"}, "quote": {"type": "string", "description": "The exact words it comes from."}},
        "required": ["field", "value", "document", "page", "quote"]}}},
    "required": ["values"],
}


def suggest_fields(fields: Sequence[str], facts: Mapping[str, str],
                   documents: Sequence[tuple[str, Sequence[str]]], factory: sessionmaker[Session] | None,
                   tenant_id: str) -> list[dict] | None:
    """Values for empty precedent fields, each quoted from a page of the matter's documents. `documents` is
    (filename, page texts). Claude fills facts only: it never writes or changes a clause."""
    if not fields or not documents:
        return []
    pages = [(d, p, text) for d, (_, texts) in enumerate(documents) for p, text in enumerate(texts, start=1)]

    def check(x: Mapping[str, Any]) -> list[str]:
        problems = []
        for i, v in enumerate(x.get("values") or []):
            if v.get("field") not in fields:
                problems.append(f"values[{i}].field is not one of the empty fields")
                continue
            source = next((t for d, p, t in pages if d == v.get("document") and p == v.get("page")), None)
            if source is None:
                problems.append(f"values[{i}] names a document page that does not exist")
            elif not quoted(v.get("quote", ""), source):
                problems.append(f"values[{i}].quote is not on document {v.get('document')} page {v.get('page')}")
            elif not str(v.get("value", "")).strip():
                problems.append(f"values[{i}].value is empty")
        return problems

    material, used = [], 0
    for d, (name, texts) in enumerate(documents):
        for p, text in enumerate(texts, start=1):
            chunk = f'<page document="{d}" name="{name}" page="{p}">\n{text}\n</page>'
            if used + len(chunk) > MAX_TEXT:
                break
            material.append(chunk)
            used += len(chunk)
    known = "\n".join(f"{k}: {v}" for k, v in facts.items() if v)
    got = _run(factory, tenant_id, "precedent_fields",
               "A firm precedent has empty fields. From the matter's documents below, submit a value for each field "
               "the documents state, with the exact words it comes from. Leave out any field the documents do not "
               "state. Do not draft or change any clause.\n\n"
               f"Empty fields: {', '.join(fields)}\n\nKnown facts:\n{known}\n\n" + "\n".join(material),
               FIELDS_SCHEMA, "Submit the field values found.", check)
    if got is None:
        return None
    out, done = [], set()
    for v in got["values"]:
        if v["field"] not in done:
            done.add(v["field"])
            out.append({"field": v["field"], "value": " ".join(v["value"].split())[:500],
                        "source": f"{documents[v['document']][0]}, page {v['page']}", "quote": v["quote"][:400]})
    return out


# ---- trust account: explaining what did not reconcile ---------------------------------------------------------------

TRUST_SCHEMA = {
    "type": "object",
    "properties": {"explanations": {"type": "array", "maxItems": 30, "items": {"type": "object", "properties": {
        "entries": {"type": "array", "items": {"type": "string"}, "description": "Cashbook ids, E1, E2…"},
        "bank_lines": {"type": "array", "items": {"type": "string"}, "description": "Bank line ids, B1, B2…"},
        "likely_cause": {"type": "string"}, "next_step": {"type": "string"}},
        "required": ["entries", "bank_lines", "likely_cause", "next_step"]}}},
    "required": ["explanations"],
}


def explain_reconciliation(outstanding: Sequence[Mapping[str, Any]], unrecorded: Sequence[Mapping[str, Any]],
                           alerts: Sequence[Mapping[str, Any]], factory: sessionmaker[Session] | None,
                           tenant_id: str) -> list[dict] | None:
    """Likely causes for the items that did not match. The difference between the items is worked out here from the
    stored amounts, never taken from Claude."""
    entries = {f"E{i}": x for i, x in enumerate(outstanding, start=1)}
    lines = {f"B{i}": x for i, x in enumerate(unrecorded, start=1)}
    if not entries and not lines:
        return []

    def check(x: Mapping[str, Any]) -> list[str]:
        problems = []
        for i, e in enumerate(x.get("explanations") or []):
            bad = [k for k in e.get("entries", []) if k not in entries] + [k for k in e.get("bank_lines", []) if k not in lines]
            if bad:
                problems.append(f"explanations[{i}] uses ids that do not exist: {', '.join(bad)}")
            if not e.get("entries") and not e.get("bank_lines"):
                problems.append(f"explanations[{i}] must name at least one item")
        return problems

    listing = "\n".join([f"{k}: cashbook {x['date']} {x['amount']} ref {x['reference']!r} client {x.get('matter') or '-'}"
                         for k, x in entries.items()] +
                        [f"{k}: bank {x['date']} {x['amount']} ref {x['reference']!r} {x['description']!r}"
                         for k, x in lines.items()])[:MAX_TEXT]
    warnings = "\n".join(a["message"] for a in alerts)[:4000]
    got = _run(factory, tenant_id, "trust_explain",
               "A client account reconciliation left these items unmatched: cashbook entries not yet on the bank "
               "statement, and bank lines not in the cashbook. Group items that probably belong together (for "
               "example a receipt less bank charges, a split payment, a reference typed wrongly) and give the likely "
               "cause and the next step for the bookkeeper. Do not state totals; the system works them out.\n\n"
               f"{listing}\n\nAlerts:\n{warnings}", TRUST_SCHEMA, "Submit the explanations.", check)
    if got is None:
        return None
    out = []
    for e in got["explanations"]:
        total = sum((Decimal(entries[k]["amount"]) for k in e["entries"]), Decimal(0))
        bank = sum((Decimal(lines[k]["amount"]) for k in e["bank_lines"]), Decimal(0))
        out.append({"entries": [entries[k] for k in e["entries"]], "bank_lines": [lines[k] for k in e["bank_lines"]],
                    "difference": str(bank - total), "likely_cause": e["likely_cause"][:600],
                    "next_step": e["next_step"][:400]})
    return out


# ---- file audit: the request to the client ------------------------------------------------------------------------

def kyc_request(client_name: str, matter: str, missing: Sequence[str], factory: sessionmaker[Session] | None,
                tenant_id: str) -> str | None:
    """A draft letter asking the client for what the file audit found missing. A person edits and sends it."""
    if not missing:
        return None
    asked = set(missing)

    def check(x: Mapping[str, Any]) -> list[str]:
        text = str(x.get("letter", ""))
        problems = [f"the letter does not ask for: {m}" for m in asked if not quoted(m, text)]
        if len(text) < 80:
            problems.append("the letter is too short")
        return problems

    got = _run(factory, tenant_id, "kyc_request",
               f"Draft a short, courteous letter from the firm to {client_name} about matter {matter}, asking for the "
               "documents below so the firm can complete its client due diligence. Name each item in exactly the words "
               "given. No legal advice. Sign off as [[advocate name]].\n\n"
               + "\n".join(f"- {m}" for m in missing),
               {"type": "object", "properties": {"letter": {"type": "string"}}, "required": ["letter"]},
               "Submit the letter.", check)
    return None if got is None else str(got["letter"]).strip()

