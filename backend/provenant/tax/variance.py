"""Cross-return variance checks: what the books say against what was declared to KRA. Pure and deterministic.

- VAT turnover (monthly VAT returns) against revenue in the mapped trial balance.
- PAYE gross pay (monthly P10 returns) against employee costs in the mapped trial balance.
- Withholding tax certificates the client issued to suppliers against payments in the purchase ledger.

A variance is not an error: exempt income, accruals and employer contributions explain many. Each check says
whether the gap is within tolerance; anything larger needs a person's explanation (ADR 0007).
"""
import csv
import io
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from provenant.core.money import ZERO, round_money, to_decimal

MAX_BYTES = 2 * 1024 * 1024
ABSOLUTE_TOLERANCE = Decimal("1000")  # rounding and small timing items
RELATIVE_TOLERANCE = Decimal("0.005")  # 0.5% of the book figure


def tolerance(book: Decimal) -> Decimal:
    return max(ABSOLUTE_TOLERANCE, round_money(abs(book) * RELATIVE_TOLERANCE))


@dataclass(frozen=True)
class VatPeriod:
    period: str  # YYYY-MM
    standard_rated: Decimal
    zero_rated: Decimal
    exempt: Decimal

    @property
    def turnover(self) -> Decimal:
        return self.standard_rated + self.zero_rated + self.exempt


@dataclass(frozen=True)
class PayePeriod:
    period: str
    gross_pay: Decimal
    paye: Decimal


@dataclass(frozen=True)
class WhtCertificate:
    certificate_number: str
    withholdee_pin: str
    withholdee_name: str
    date: date
    gross_amount: Decimal
    wht_amount: Decimal


@dataclass(frozen=True)
class Row:
    label: str
    book: Decimal
    declared: Decimal

    @property
    def variance(self) -> Decimal:
        return self.book - self.declared


@dataclass(frozen=True)
class Check:
    key: str  # vat_turnover | paye | wht
    title: str
    book_label: str
    declared_label: str
    book: Decimal
    declared: Decimal
    rows: tuple[Row, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def variance(self) -> Decimal:
        return self.book - self.declared

    @property
    def tolerance(self) -> Decimal:
        return tolerance(self.book)

    @property
    def needs_explanation(self) -> bool:
        return abs(self.variance) > self.tolerance or any(abs(r.variance) > tolerance(r.book) for r in self.rows
                                                          if self.key == "wht")


# --- Reading the returns ---------------------------------------------------------------------------------------


def _rows(data: bytes, what: str, required: tuple[str, ...]) -> list[tuple[int, dict[str, str]]]:
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
    rows = []
    for number, raw in enumerate(reader, start=2):
        row = {k.strip().lower(): (v or "").strip() for k, v in raw.items() if k}
        if any(row.values()):
            rows.append((number, row))
    if not rows:
        raise ValueError(f"{what} has no rows")
    return rows


def _money(row: dict[str, str], name: str, number: int) -> Decimal:
    try:
        return to_decimal(row.get(name) or "0")
    except (ValueError, TypeError) as exc:
        raise ValueError(f"row {number}: {name}: {exc}") from exc


def _period(value: str, number: int, seen: set[str]) -> str:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
        raise ValueError(f"row {number}: period must look like 2026-03, not {value!r}")
    if value in seen:
        raise ValueError(f"row {number}: period {value} appears twice")
    seen.add(value)
    return value


def read_vat(data: bytes) -> list[VatPeriod]:
    seen: set[str] = set()
    return [
        VatPeriod(_period(row["period"], n, seen), _money(row, "standard_rated_sales", n),
                  _money(row, "zero_rated_sales", n), _money(row, "exempt_sales", n))
        for n, row in _rows(data, "VAT returns", ("period", "standard_rated_sales", "zero_rated_sales", "exempt_sales"))
    ]


def read_paye(data: bytes) -> list[PayePeriod]:
    seen: set[str] = set()
    return [
        PayePeriod(_period(row["period"], n, seen), _money(row, "gross_pay", n), _money(row, "paye", n))
        for n, row in _rows(data, "PAYE returns", ("period", "gross_pay", "paye"))
    ]


def read_wht(data: bytes) -> list[WhtCertificate]:
    certs, seen = [], set()
    for n, row in _rows(data, "withholding tax certificates",
                        ("certificate_number", "withholdee_pin", "date", "gross_amount", "wht_amount")):
        number = row["certificate_number"][:64]
        if not number or number in seen:
            raise ValueError(f"row {n}: certificate number is empty or appears twice")
        seen.add(number)
        try:
            when = date.fromisoformat(row["date"])
        except ValueError as exc:
            raise ValueError(f"row {n}: date must look like 2026-03-31") from exc
        certs.append(WhtCertificate(number, row["withholdee_pin"].upper(), row.get("withholdee_name", "")[:200], when,
                                    _money(row, "gross_amount", n), _money(row, "wht_amount", n)))
    return certs


# --- The checks ------------------------------------------------------------------------------------------------


def year_months(year_start: date, year_end: date) -> list[str]:
    months, y, m = [], year_start.year, year_start.month
    while (y, m) <= (year_end.year, year_end.month):
        months.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return months


def _coverage(periods: Iterable[str], months: Sequence[str], what: str) -> tuple[str, ...]:
    have = set(periods)
    missing = [m for m in months if m not in have]
    outside = sorted(have - set(months))
    notes = []
    if missing:
        notes.append(f"{len(missing)} of {len(months)} {what} missing: {', '.join(missing)}")
    if outside:
        notes.append(f"{what} outside the year ignored: {', '.join(outside)}")
    return tuple(notes)


def vat_turnover(revenue: Decimal, periods: Sequence[VatPeriod], months: Sequence[str]) -> Check:
    """Revenue in the books (positive) against turnover declared on the year's VAT returns."""
    in_year = [p for p in periods if p.period in months]
    declared = sum((p.turnover for p in in_year), ZERO)
    return Check(
        "vat_turnover", "VAT turnover against revenue", "Revenue in the trial balance", "Turnover on VAT returns",
        revenue, declared, tuple(Row(p.period, ZERO, p.turnover) for p in in_year),
        _coverage((p.period for p in periods), months, "VAT returns"),
    )


def paye(employee_costs: Decimal, periods: Sequence[PayePeriod], months: Sequence[str]) -> Check:
    """Employee costs in the books against gross pay on the year's PAYE returns. Employer pension, NSSF and
    medical contributions in staff costs are a common, explainable difference."""
    in_year = [p for p in periods if p.period in months]
    return Check(
        "paye", "PAYE gross pay against staff costs", "Employee costs in the trial balance", "Gross pay on PAYE returns",
        employee_costs, sum((p.gross_pay for p in in_year), ZERO),
        tuple(Row(p.period, ZERO, p.gross_pay) for p in in_year),
        _coverage((p.period for p in periods), months, "PAYE returns"),
    )


def wht_on_payments(payments: Iterable[tuple[str, str, Decimal]], certificates: Sequence[WhtCertificate],
                    year_start: date, year_end: date) -> Check:
    """For each supplier the client withheld tax from: payments in the purchase ledger (book) against the gross
    amounts on the certificates issued (declared). Suppliers without certificates are not listed: whether a
    payment attracts withholding depends on its nature, which the ledger does not say."""
    in_year = [c for c in certificates if year_start <= c.date <= year_end]
    paid: dict[str, Decimal] = {}
    names: dict[str, str] = {}
    for pin, name, amount in payments:
        paid[pin] = paid.get(pin, ZERO) + amount
        names.setdefault(pin, name)
    certified: dict[str, Decimal] = {}
    for c in in_year:
        certified[c.withholdee_pin] = certified.get(c.withholdee_pin, ZERO) + c.gross_amount
        names.setdefault(c.withholdee_pin, c.withholdee_name)
    rows = tuple(Row(f"{names.get(pin) or pin} ({pin})", paid.get(pin, ZERO), gross)
                 for pin, gross in sorted(certified.items()))
    outside = len(certificates) - len(in_year)
    notes = (f"{outside} certificates dated outside the year ignored",) if outside else ()
    return Check(
        "wht", "Withholding tax certificates against supplier payments", "Payments in the purchase ledger",
        "Gross on WHT certificates", sum((r.book for r in rows), ZERO), sum((r.declared for r in rows), ZERO),
        rows, notes,
    )
