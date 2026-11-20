"""eTIMS reconciliation: match a client's purchase ledger to its eTIMS purchase invoices. Pure and deterministic.

Kenya requires expenditure to be supported by an electronic tax invoice (eTIMS) to be deductible, subject to
exemptions (the provision and its exemptions are confirmed by the preparer for the year; see ADR 0007).
Before a desk audit, a consultant wants every purchase posted to a deductible account matched to an eTIMS
invoice, or marked exempt with a reason, and the rest quantified.

Purchase ledger CSV: date, supplier, supplier_pin, invoice_number, account_code, amount (excluding VAT);
optional etims_number (control unit invoice number recorded in the ledger), description.
eTIMS export CSV: cu_invoice_number, supplier_pin, invoice_number, date, taxable_value; optional
supplier_name, vat.
"""
import csv
import io
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from provenant.core.money import ZERO, to_decimal

MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 50_000
TOLERANCE = Decimal("1.00")  # rounding between ledger and eTIMS values
PROBABLE_DAYS = 7  # a same-supplier, same-amount invoice within a week is a probable match, never a match


@dataclass(frozen=True)
class PurchaseLine:
    number: int  # CSV row number
    date: date
    supplier: str
    supplier_pin: str
    invoice_number: str
    account_code: str
    amount: Decimal  # excluding VAT, as posted to the account
    etims_number: str = ""
    description: str = ""

    @property
    def key(self) -> str:
        """Stable across re-uploads of the same ledger, so a person's decisions carry over."""
        return f"{self.supplier_pin}|{normalise(self.invoice_number)}|{self.amount}"


@dataclass(frozen=True)
class EtimsInvoice:
    number: int
    cu_invoice_number: str
    supplier_pin: str
    invoice_number: str
    date: date
    taxable_value: Decimal
    vat: Decimal = ZERO
    supplier_name: str = ""


class Status(StrEnum):
    MATCHED = "matched"
    DIFFERS = "differs"  # an eTIMS invoice exists but the amount or supplier PIN differs
    PROBABLE = "probable"  # same supplier and amount, near date: a person must confirm it
    UNMATCHED = "unmatched"  # no eTIMS invoice found
    EXEMPT = "exempt"  # a person decided it needs no eTIMS invoice, and why


@dataclass(frozen=True)
class Decision:
    """A person's decision on one ledger line: exempt (with reason), or matched to a named eTIMS invoice."""

    kind: str  # "exempt" | "match"
    reason: str = ""
    cu_invoice_number: str = ""
    by: str = ""

    def __post_init__(self) -> None:
        if self.kind not in ("exempt", "match"):
            raise ValueError("decision must be exempt or match")
        if self.kind == "exempt" and len(self.reason.strip()) < 5:
            raise ValueError("say why the purchase needs no eTIMS invoice (e.g. salaries, bank charges, imports)")
        if self.kind == "match" and not self.cu_invoice_number.strip():
            raise ValueError("name the eTIMS invoice it matches")


@dataclass(frozen=True)
class LineResult:
    line: PurchaseLine
    status: Status
    invoice: EtimsInvoice | None = None
    note: str = ""
    decided_by: str = ""

    @property
    def unsupported(self) -> Decimal:
        """The part of the line no eTIMS invoice supports: all of it if unmatched, the excess if it differs."""
        if self.status is Status.UNMATCHED:
            return self.line.amount
        if self.status is Status.DIFFERS and self.invoice is not None:
            return max(self.line.amount - self.invoice.taxable_value, ZERO)
        return ZERO


@dataclass(frozen=True)
class Reconciliation:
    lines: tuple[LineResult, ...]
    etims_only: tuple[EtimsInvoice, ...]  # in eTIMS but not in the ledger: unrecorded purchases or input VAT

    def total(self, status: Status) -> Decimal:
        return sum((r.line.amount for r in self.lines if r.status is status), ZERO)

    @property
    def unsupported(self) -> Decimal:
        return sum((r.unsupported for r in self.lines), ZERO)


def normalise(text: str) -> str:
    return re.sub(r"[^0-9A-Z]", "", text.upper())


def _pin(text: str) -> str:
    pin = text.strip().upper()
    if pin and not re.fullmatch(r"[AP]\d{9}[A-Z]", pin):
        raise ValueError(f"{text!r} is not a KRA PIN (e.g. P051234567X)")
    return pin


def _rows(data: bytes, what: str, required: tuple[str, ...]) -> Iterable[tuple[int, dict[str, str]]]:
    if len(data) > MAX_BYTES:
        raise ValueError(f"{what} exceeds {MAX_BYTES // (1024 * 1024)} MB")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{what} must be a UTF-8 CSV file") from exc
    reader = csv.DictReader(io.StringIO(text))
    headers = {h.strip().lower() for h in reader.fieldnames or []}
    missing = [c for c in required if c not in headers]
    if missing:
        raise ValueError(f"{what}: columns needed: {', '.join(required)}; missing {', '.join(missing)}")
    count = 0
    for number, raw in enumerate(reader, start=2):
        row = {k.strip().lower(): (v or "").strip() for k, v in raw.items() if k}
        if not any(row.values()):
            continue
        count += 1
        if count > MAX_ROWS:
            raise ValueError(f"{what} has more than {MAX_ROWS} rows")
        yield number, row


def _date(value: str, number: int) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"row {number}: date must look like 2026-03-31, not {value!r}") from exc


def _amount(value: str, what: str, number: int) -> Decimal:
    try:
        return to_decimal(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"row {number}: {what}: {exc}") from exc


LEDGER_COLUMNS = ("date", "supplier", "supplier_pin", "invoice_number", "account_code", "amount")
ETIMS_COLUMNS = ("cu_invoice_number", "supplier_pin", "invoice_number", "date", "taxable_value")


def read_ledger(data: bytes) -> list[PurchaseLine]:
    lines = []
    for number, row in _rows(data, "purchase ledger", LEDGER_COLUMNS):
        try:
            pin = _pin(row["supplier_pin"])
        except ValueError as exc:
            raise ValueError(f"row {number}: {exc}") from exc
        if not row["account_code"]:
            raise ValueError(f"row {number}: account code is empty")
        lines.append(PurchaseLine(
            number, _date(row["date"], number), row["supplier"][:200], pin, row["invoice_number"][:64],
            row["account_code"][:64], _amount(row["amount"], "amount", number),
            row.get("etims_number", "")[:64], row.get("description", "")[:255],
        ))
    if not lines:
        raise ValueError("the purchase ledger has no rows")
    return lines


def read_etims(data: bytes) -> list[EtimsInvoice]:
    invoices, seen = [], set()
    for number, row in _rows(data, "eTIMS export", ETIMS_COLUMNS):
        cu = row["cu_invoice_number"][:64]
        if not cu:
            raise ValueError(f"row {number}: control unit invoice number is empty")
        if normalise(cu) in seen:
            raise ValueError(f"row {number}: control unit invoice {cu!r} appears twice")
        seen.add(normalise(cu))
        try:
            pin = _pin(row["supplier_pin"])
        except ValueError as exc:
            raise ValueError(f"row {number}: {exc}") from exc
        invoices.append(EtimsInvoice(
            number, cu, pin, row["invoice_number"][:64], _date(row["date"], number),
            _amount(row["taxable_value"], "taxable value", number),
            _amount(row["vat"], "VAT", number) if row.get("vat") else ZERO, row.get("supplier_name", "")[:200],
        ))
    if not invoices:
        raise ValueError("the eTIMS export has no rows")
    return invoices


def _compare(line: PurchaseLine, invoice: EtimsInvoice, how: str) -> tuple[Status, str]:
    problems = []
    if abs(line.amount - invoice.taxable_value) > TOLERANCE:
        problems.append(f"amount {line.amount} vs eTIMS {invoice.taxable_value}")
    if line.supplier_pin and invoice.supplier_pin and line.supplier_pin != invoice.supplier_pin:
        problems.append(f"supplier PIN {line.supplier_pin} vs eTIMS {invoice.supplier_pin}")
    if problems:
        return Status.DIFFERS, f"{how}; " + "; ".join(problems)
    return Status.MATCHED, how


def reconcile(
    lines: Iterable[PurchaseLine], invoices: Iterable[EtimsInvoice], decisions: Mapping[str, Decision] | None = None,
) -> Reconciliation:
    """Match each ledger line to at most one eTIMS invoice, and each invoice to at most one line.

    Order: a person's decision; the control unit number recorded in the ledger; supplier PIN and invoice number;
    then supplier PIN, amount and a date within a week, which is only ever "probable".
    """
    decisions = decisions or {}
    lines, invoices = list(lines), list(invoices)
    by_cu = {normalise(i.cu_invoice_number): i for i in invoices}
    by_ref: dict[tuple[str, str], list[EtimsInvoice]] = {}
    for i in invoices:
        by_ref.setdefault((i.supplier_pin, normalise(i.invoice_number)), []).append(i)
    used: set[str] = set()

    def take(invoice: EtimsInvoice | None) -> EtimsInvoice | None:
        if invoice is None or invoice.cu_invoice_number in used:
            return None
        used.add(invoice.cu_invoice_number)
        return invoice

    results: dict[int, LineResult] = {}
    # Pass 1: decisions and exact references, so a later fuzzy match never steals a referenced invoice.
    for line in lines:
        decision = decisions.get(line.key)
        if decision and decision.kind == "exempt":
            results[line.number] = LineResult(line, Status.EXEMPT, note=decision.reason, decided_by=decision.by)
            continue
        if decision and decision.kind == "match":
            invoice = take(by_cu.get(normalise(decision.cu_invoice_number)))
            if invoice is None:
                results[line.number] = LineResult(
                    line, Status.UNMATCHED, note=f"confirmed match {decision.cu_invoice_number} is not in the eTIMS export "
                    "(or is already matched)", decided_by=decision.by)
            else:
                status, note = _compare(line, invoice, "matched by a person")
                results[line.number] = LineResult(line, status, invoice, note, decision.by)
            continue
        invoice = take(by_cu.get(normalise(line.etims_number))) if line.etims_number else None
        how = "control unit number in the ledger"
        if invoice is None:
            candidates = [i for i in by_ref.get((line.supplier_pin, normalise(line.invoice_number)), [])
                          if i.cu_invoice_number not in used]
            invoice = take(candidates[0]) if len(candidates) == 1 and line.invoice_number else None
            how = "supplier PIN and invoice number"
        if invoice is not None:
            status, note = _compare(line, invoice, how)
            results[line.number] = LineResult(line, status, invoice, note)

    # Pass 2: probable matches for what is left, only when exactly one invoice fits.
    for line in lines:
        if line.number in results:
            continue
        candidates = [
            i for i in invoices
            if i.cu_invoice_number not in used and i.supplier_pin == line.supplier_pin
            and abs(i.taxable_value - line.amount) <= TOLERANCE and abs((i.date - line.date).days) <= PROBABLE_DAYS
        ]
        if len(candidates) == 1:
            invoice = take(candidates[0])
            results[line.number] = LineResult(
                line, Status.PROBABLE, invoice,
                f"same supplier and amount, {abs((invoice.date - line.date).days)} days apart: confirm or reject")
        else:
            note = f"{len(candidates)} possible eTIMS invoices: match one by hand" if candidates else "no eTIMS invoice found"
            results[line.number] = LineResult(line, Status.UNMATCHED, note=note)

    return Reconciliation(
        lines=tuple(results[line.number] for line in lines),
        etims_only=tuple(i for i in invoices if i.cu_invoice_number not in used),
    )
