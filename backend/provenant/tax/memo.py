"""Board tax memo: prose with every figure bound to the computation. Pure and deterministic.

A memo is text with fields in double braces, {{taxable_profit}}, and table blocks on a line of their own,
{{table:computation}}. Rendering replaces each field with the computation's current value and keeps its source,
so changing an adjustment updates every paragraph that mentions it. Amounts typed by hand are flagged: a figure
that is not bound is the one that goes stale (ADR 0008).
"""
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from provenant.tax.engine import Computation

FIELD = re.compile(r"\{\{\s*([a-z_]+(?::[a-z_]+)?)\s*\}\}")
# Amounts with thousands separators, or 5+ digits; years and short references (s.16, 2026) are left alone.
TYPED_NUMBER = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{5,}(?:\.\d+)?)(?![\w])")
TABLES = ("computation", "deferred", "checks")
FIELD_NAMES = (
    "client", "year_end", "profit_before_tax", "taxable_before_losses", "losses_brought_forward", "losses_used",
    "losses_carried_forward", "taxable_profit", "rate", "tax_payable", "total_credits", "tax_due", "tax_due_sentence",
    "etims_addback", "deferred_tax", "deferred_tax_kind", "approval_status", "open_checks", "rules_version",
)


@dataclass(frozen=True)
class Field:
    label: str
    text: str  # as shown in the memo
    source: str  # where the value comes from


def money(value: Decimal) -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}{abs(value):,.2f}"


def fields(c: Computation, client: str, year_end: str, currency: str = "KES",
           approval: str = "", open_checks: int = 0) -> dict[str, Field]:
    """The values a memo may cite, each with its source in the computation."""
    credits = -sum((x.amount for x in c.credits), Decimal(0))
    etims = next((a.amount for a in c.adjustments if "eTIMS" in a.label), Decimal(0))
    m = lambda v: f"{currency} {money(v)}"  # noqa: E731
    if not c.credits:
        due = f"no credits are recorded, so tax payable of {m(c.tax_payable)} is due"
    elif c.tax_due >= 0:
        due = f"the balance due to KRA is {m(c.tax_due)}"
    else:
        due = f"KRA owes a refund of {m(-c.tax_due)}"
    return {
        "client": Field("Client", client, "client record"),
        "year_end": Field("Year end", year_end, "financial year"),
        "profit_before_tax": Field("Profit before tax", m(c.profit_before_tax), "computation: profit before tax"),
        "taxable_before_losses": Field("Taxable profit before losses", m(c.taxable_before_losses),
                                       "computation: taxable profit before losses"),
        "losses_brought_forward": Field("Losses brought forward", m(c.losses_brought_forward), "preparer inputs"),
        "losses_used": Field("Losses used", m(c.losses_used), "computation: losses used"),
        "losses_carried_forward": Field("Losses carried forward", m(c.losses_carried_forward),
                                        "computation: losses carried forward"),
        "taxable_profit": Field("Taxable profit", m(c.taxable_profit), "computation: taxable profit"),
        "rate": Field("Tax rate", f"{(c.rate * 100).normalize():f}%", f"rules {c.rules_version}"),
        "tax_payable": Field("Tax payable", m(c.tax_payable), "computation: tax payable"),
        "total_credits": Field("Credits", m(credits), "computation: credits"),
        "tax_due": Field("Tax due (negative: refundable)", m(c.tax_due), "computation: tax due"),
        "tax_due_sentence": Field("Tax due, in words", due, "computation: tax due"),
        "etims_addback": Field("eTIMS add-back", m(etims), "computation: expenses without an eTIMS invoice"),
        "deferred_tax": Field("Deferred tax", m(abs(c.deferred_tax)), "computation: deferred tax"),
        "deferred_tax_kind": Field("Deferred tax: liability or asset",
                                   "liability" if c.deferred_tax >= 0 else "asset", "computation: deferred tax"),
        "approval_status": Field("Approval status", approval or "The computation has not been approved.",
                                 "tax approvals"),
        "open_checks": Field("Unexplained variances", str(open_checks), "compliance checks"),
        "rules_version": Field("Rules version", c.rules_version, "rules"),
    }


@dataclass(frozen=True)
class Segment:
    text: str
    field: str | None = None  # the field it was bound to


@dataclass(frozen=True)
class Block:
    kind: str  # heading1 | heading2 | paragraph | bullet | table
    segments: tuple[Segment, ...] = ()
    table: str | None = None
    rows: tuple[tuple[str, str, bool], ...] = ()  # (label, amount, is_total)


@dataclass(frozen=True)
class Rendered:
    blocks: tuple[Block, ...]
    warnings: tuple[str, ...] = field(default_factory=tuple)
    used: tuple[str, ...] = ()  # fields cited, in order


def tables(c: Computation, checks: Sequence[tuple[str, str]], currency: str = "KES") -> dict[str, tuple]:
    comp = [("Profit before tax", money(c.profit_before_tax), True)]
    comp += [(a.label, money(a.amount), False) for a in c.adjustments]
    comp += [("Taxable profit before losses", money(c.taxable_before_losses), True)]
    if c.losses_used:
        comp += [("Tax losses brought forward used", money(-c.losses_used), False)]
    comp += [("Taxable profit", money(c.taxable_profit), True),
             (f"Corporation tax at {(c.rate * 100).normalize():f}%", money(c.tax_payable), True)]
    comp += [(f"Less: {x.label.lower()}", money(x.amount), False) for x in c.credits]
    if c.credits:
        comp += [("Tax due" if c.tax_due >= 0 else "Tax refundable", money(abs(c.tax_due)), True)]
    deferred = [(d.label, money(d.amount), False) for d in c.deferred]
    deferred += [("Deferred tax " + ("liability" if c.deferred_tax >= 0 else "asset"), money(abs(c.deferred_tax)), True)]
    return {"computation": tuple(comp), "deferred": tuple(deferred),
            "checks": tuple((name, status, False) for name, status in checks)}


def render(text: str, values: Mapping[str, Field], table_rows: Mapping[str, tuple]) -> Rendered:
    """Turn memo text into blocks. Unknown fields are errors; typed amounts are warnings."""
    blocks: list[Block] = []
    warnings: list[str] = []
    used: list[str] = []
    unknown = sorted({name for name in FIELD.findall(text)
                      if not (name.startswith("table:") and name[6:] in TABLES) and name not in values})
    if unknown:
        raise ValueError(f"unknown fields: {', '.join(unknown)} (choose from the list of fields)")

    def segments(line: str) -> tuple[Segment, ...]:
        out, pos = [], 0
        for match in FIELD.finditer(line):
            if match.start() > pos:
                out.append(Segment(line[pos:match.start()]))
            name = match.group(1)
            out.append(Segment(values[name].text, name))
            used.append(name)
            pos = match.end()
        if pos < len(line):
            out.append(Segment(line[pos:]))
        for s in out:
            if s.field is None:
                for number in TYPED_NUMBER.findall(s.text):
                    warnings.append(f"'{number}' is typed, not bound to the computation: insert a field instead")
        return tuple(out)

    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            blocks.append(Block("paragraph", segments(" ".join(paragraph))))
            paragraph.clear()

    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        table = re.fullmatch(r"\{\{\s*table:([a-z_]+)\s*\}\}", line)
        if not line:
            flush()
        elif table:
            flush()
            blocks.append(Block("table", table=table.group(1), rows=table_rows.get(table.group(1), ())))
            used.append(f"table:{table.group(1)}")
        elif line.startswith("## "):
            flush()
            blocks.append(Block("heading2", segments(line[3:])))
        elif line.startswith("# "):
            flush()
            blocks.append(Block("heading1", segments(line[2:])))
        elif line.startswith("- "):
            flush()
            blocks.append(Block("bullet", segments(line[2:])))
        else:
            paragraph.append(line)
    flush()
    return Rendered(tuple(blocks), tuple(dict.fromkeys(warnings)), tuple(dict.fromkeys(used)))


DEFAULT = """# Corporation tax: {{client}}, year ended {{year_end}}

## Summary
Profit before tax is {{profit_before_tax}}. After the adjustments below, taxable profit is {{taxable_profit}}, and corporation tax at {{rate}} is {{tax_payable}}. After credits of {{total_credits}}, {{tax_due_sentence}}.

## Computation
{{table:computation}}

## Losses
Tax losses of {{losses_used}} were used this year; {{losses_carried_forward}} carry forward.

## Deferred tax
{{table:deferred}}
The deferred tax {{deferred_tax_kind}} is {{deferred_tax}} at {{rate}}.

## Compliance
Expenses without an eTIMS invoice added back: {{etims_addback}}.
{{table:checks}}

## Status
{{approval_status}}

This memo is a working paper prepared from the computation (rules {{rules_version}}). It is not tax advice: rates, deductions and treatments must be confirmed against the law in force for the year.
"""
