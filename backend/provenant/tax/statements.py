"""M-Pesa and bank statements: parse, de-duplicate, check running balances, pair own-account transfers and
suggest categories for a person to confirm. Pure and deterministic.

Kenyan SMEs keep much of their trade on M-Pesa Till/Paybill accounts alongside bank accounts. Vouching them by
hand takes days; the checks a consultant needs are mechanical: is every row there (the running balance never
jumps), is anything counted twice (duplicates, and sweeps between the client's own accounts), and what is each
movement (sales receipts, supplier payments, salaries, KRA payments, charges).

M-Pesa: the business portal's CSV export (Receipt No., Completion Time, Details, Paid In, Withdrawn, Balance,
optionally Transaction Status and Other Party Info). Bank: a CSV with date, description and either amount
(credit positive) or debit and credit columns; optionally reference and balance.
"""
import csv
import io
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from provenant.core.money import ZERO, to_decimal

MAX_BYTES = 10 * 1024 * 1024
MAX_ROWS = 100_000
TRANSFER_DAYS = 3  # a sweep from M-Pesa to the bank usually lands within a few days

CATEGORIES = {
    "sales_receipt": "Sales receipts",
    "other_receipt": "Other receipts",
    "supplier_payment": "Supplier payments",
    "salaries": "Salaries and wages",
    "tax_payment": "KRA tax payments",
    "statutory": "NSSF, SHIF and housing levy",
    "charges": "Bank and M-Pesa charges",
    "loan": "Loans and repayments",
    "owner": "Owner capital and drawings",
    "transfer": "Transfer between own accounts",
    "other_payment": "Other payments",
}

# (pattern on the lower-cased description, category when money comes in, category when it goes out). First match
# wins: specific before general, as for account mappings.
_RULES: tuple[tuple[str, str | None, str | None], ...] = (
    (r"charge|commission|ledger fee|excise duty|service fee", None, "charges"),
    (r"\bkra\b|kenya revenue|domestic tax|\bitax\b|\bprn\b", None, "tax_payment"),
    (r"\bnssf\b|\bshif\b|\bnhif\b|housing levy|\bahl\b", None, "statutory"),
    (r"salary|salaries|payroll|wages|net pay", None, "salaries"),
    (r"loan|fuliza|m-shwari|overdraft|interest", "loan", "loan"),
    (r"transfer (to|from) (bank|own|account)|sweep|funds transfer|b2b transfer|settlement", "transfer", "transfer"),
    (r"drawings|director|capital injection|shareholder", "owner", "owner"),
    (r"customer payment|merchant payment|pay bill.*from|buy goods.*from|lipa na m-?pesa|deposit of funds|"
     r"sales|invoice|cash deposit", "sales_receipt", None),
    (r"business payment to|pay bill online|pay merchant|supplier|purchase|lpo|rtgs|eft", None, "supplier_payment"),
)


@dataclass(frozen=True)
class Txn:
    source: str  # the statement's label, e.g. "M-Pesa Till 123456" or "Equity Bank 0123..."
    kind: str  # mpesa | bank
    row: int  # CSV row number
    date: date
    reference: str
    description: str
    amount: Decimal  # money in positive, money out negative
    balance: Decimal | None = None
    counterparty: str = ""

    @property
    def key(self) -> str:
        """Stable across re-uploads: the receipt or bank reference, else date, amount and description."""
        if self.reference:
            return f"{self.kind}:{_norm(self.reference)}"
        return f"{self.kind}:{self.date.isoformat()}|{self.amount}|{_norm(self.description)[:60]}"


def _norm(text: str) -> str:
    return re.sub(r"[^0-9a-z]", "", text.lower())


def _rows(data: bytes, what: str) -> tuple[list[str], list[tuple[int, dict[str, str]]]]:
    if len(data) > MAX_BYTES:
        raise ValueError(f"{what} exceeds {MAX_BYTES // (1024 * 1024)} MB")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{what} must be a UTF-8 CSV file") from exc
    lines = text.splitlines()
    # Portal exports put a few summary lines above the table: start at the header row.
    start = next((i for i, line in enumerate(lines[:30]) if re.search(r"(?i)receipt no|date", line)), 0)
    reader = csv.DictReader(io.StringIO("\n".join(lines[start:])))
    headers = [h.strip().lower() for h in reader.fieldnames or []]
    rows = []
    for number, raw in enumerate(reader, start=start + 2):
        row = {k.strip().lower(): (v or "").strip() for k, v in raw.items() if k}
        if any(row.values()):
            rows.append((number, row))
        if len(rows) > MAX_ROWS:
            raise ValueError(f"{what} has more than {MAX_ROWS} rows")
    if not rows:
        raise ValueError(f"{what} has no rows")
    return headers, rows


def _col(headers: Sequence[str], *names: str) -> str | None:
    for name in names:
        if name in headers:
            return name
    return None


def _money(value: str, what: str, number: int) -> Decimal:
    value = value.replace("KES", "").replace("Ksh", "").strip()
    if not value or value in ("-", "--"):
        return ZERO
    negative = value.startswith("(") and value.endswith(")")
    try:
        amount = to_decimal(value.strip("()").lstrip("+"))
    except (ValueError, TypeError) as exc:
        raise ValueError(f"row {number}: {what}: {exc}") from exc
    return -amount if negative else amount


def _when(value: str, number: int) -> date:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d-%m-%Y %H:%M:%S", "%d/%m/%Y %H:%M:%S",
                "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d-%b-%Y"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"row {number}: unreadable date {value!r} (use YYYY-MM-DD or DD/MM/YYYY)")


def read_mpesa(data: bytes, source: str) -> list[Txn]:
    headers, rows = _rows(data, "M-Pesa statement")
    need = {"receipt no.": _col(headers, "receipt no.", "receipt no", "receipt"),
            "completion time": _col(headers, "completion time", "date"),
            "details": _col(headers, "details", "description"),
            "paid in": _col(headers, "paid in", "paid in (ksh)"),
            "withdrawn": _col(headers, "withdrawn", "withdrawn (ksh)")}
    missing = [k for k, v in need.items() if v is None]
    if missing:
        raise ValueError(f"M-Pesa statement: columns needed: Receipt No., Completion Time, Details, Paid In, "
                         f"Withdrawn (and Balance); missing {', '.join(missing)}")
    status, balance = _col(headers, "transaction status", "status"), _col(headers, "balance")
    party = _col(headers, "other party info", "other party")
    out = []
    for number, row in rows:
        if status and row.get(status, "").lower() not in ("", "completed"):
            continue  # failed or reversed attempts never moved money
        paid_in = _money(row[need["paid in"]], "Paid In", number)
        withdrawn = abs(_money(row[need["withdrawn"]], "Withdrawn", number))
        out.append(Txn(source, "mpesa", number, _when(row[need["completion time"]], number),
                       row[need["receipt no."]][:64], row[need["details"]][:255], paid_in - withdrawn,
                       _money(row[balance], "Balance", number) if balance and row.get(balance) else None,
                       (row.get(party, "") if party else "")[:200]))
    return out


def read_bank(data: bytes, source: str) -> list[Txn]:
    headers, rows = _rows(data, "bank statement")
    when = _col(headers, "date", "transaction date", "value date", "posting date")
    desc = _col(headers, "description", "narration", "details", "particulars")
    amount = _col(headers, "amount")
    debit, credit = _col(headers, "debit", "withdrawals", "money out"), _col(headers, "credit", "deposits", "money in")
    if not when or not desc or not (amount or (debit and credit)):
        raise ValueError("bank statement: columns needed: date, description, and amount (or debit and credit)")
    ref, balance = _col(headers, "reference", "ref", "cheque no", "transaction id"), _col(headers, "balance")
    out = []
    for number, row in rows:
        if amount:
            value = _money(row[amount], "amount", number)
        else:
            value = _money(row[credit], "credit", number) - abs(_money(row[debit], "debit", number))
        out.append(Txn(source, "bank", number, _when(row[when], number), (row.get(ref, "") if ref else "")[:64],
                       row[desc][:255], value,
                       _money(row[balance], "balance", number) if balance and row.get(balance) else None))
    return out


# --- Checks ------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class BalanceBreak:
    source: str
    row: int
    expected: Decimal
    stated: Decimal

    @property
    def gap(self) -> Decimal:
        return self.stated - self.expected


def balance_breaks(txns: Sequence[Txn]) -> list[BalanceBreak]:
    """Where a statement's running balance does not follow from the previous row: a missing or altered row.
    Checked in the statement's own row order, in whichever direction it runs (oldest or newest first)."""
    breaks: list[BalanceBreak] = []
    by_source: dict[str, list[Txn]] = {}
    for t in txns:
        by_source.setdefault(t.source, []).append(t)
    for source, rows in by_source.items():
        rows = [t for t in sorted(rows, key=lambda t: t.row) if t.balance is not None]
        if len(rows) < 2:
            continue
        # M-Pesa exports list newest first, bank statements usually oldest first: check both, keep the direction
        # that fits (a genuine gap shows in either).
        def walk(ordered: list[Txn]) -> list[BalanceBreak]:
            return [BalanceBreak(source, cur.row, prev.balance + cur.amount, cur.balance)
                    for prev, cur in zip(ordered, ordered[1:]) if prev.balance + cur.amount != cur.balance]

        breaks.extend(min(walk(rows), walk(list(reversed(rows))), key=len))
    return breaks


@dataclass(frozen=True)
class Statements:
    txns: tuple[Txn, ...]  # unique movements, in source then row order
    duplicates: tuple[Txn, ...]  # dropped: same key as an earlier row
    transfers: tuple[tuple[Txn, Txn], ...]  # (money out, money in) between the client's own accounts
    breaks: tuple[BalanceBreak, ...] = field(default_factory=tuple)


def consolidate(statements: Iterable[Sequence[Txn]]) -> Statements:
    """De-duplicate across uploads, check running balances, and pair own-account transfers: an outflow from one
    statement and an equal inflow to another within a few days (each movement paired at most once)."""
    all_rows = [t for s in statements for t in s]
    seen: set[str] = set()
    unique, duplicates = [], []
    for t in all_rows:
        (duplicates if t.key in seen else unique).append(t)
        seen.add(t.key)
    pairs, used = [], set()
    for out in sorted((t for t in unique if t.amount < 0), key=lambda t: (t.date, t.source, t.row)):
        match = next((i for i in sorted(unique, key=lambda t: (t.date, t.source, t.row))
                      if i.amount == -out.amount and i.source != out.source and i.key not in used
                      and 0 <= (i.date - out.date).days <= TRANSFER_DAYS), None)
        if match is not None and out.key not in used:
            pairs.append((out, match))
            used.update({out.key, match.key})
    return Statements(tuple(unique), tuple(duplicates), tuple(pairs), tuple(balance_breaks(all_rows)))


def suggest(txn: Txn) -> str | None:
    text = f"{txn.description} {txn.counterparty}".lower()
    for pattern, money_in, money_out in _RULES:
        category = money_in if txn.amount > 0 else money_out
        if category and re.search(pattern, text):
            return category
    return None


@dataclass(frozen=True)
class Categorised:
    txn: Txn
    category: str | None
    status: str  # suggested | confirmed | unassigned
    rule: str = ""  # why: "transfer pair", "keyword", or the person who confirmed it


def categorise(result: Statements, confirmed: Mapping[str, tuple[str, str]]) -> list[Categorised]:
    """A person's confirmed category wins; then own-account transfer pairs; then keyword suggestions."""
    paired = {t.key for pair in result.transfers for t in pair}
    out = []
    for t in result.txns:
        if not t.amount:
            continue  # an opening or balance-only row: kept for the balance check, nothing to categorise
        if t.key in confirmed:
            category, by = confirmed[t.key]
            out.append(Categorised(t, category, "confirmed", by))
        elif t.key in paired:
            out.append(Categorised(t, "transfer", "suggested", "transfer pair"))
        elif (category := suggest(t)) is not None:
            out.append(Categorised(t, category, "suggested", "keyword"))
        else:
            out.append(Categorised(t, None, "unassigned"))
    return out


def totals(items: Iterable[Categorised]) -> dict[str, tuple[Decimal, Decimal]]:
    """Money in and money out per category (None: unassigned)."""
    sums: dict[str, tuple[Decimal, Decimal]] = {}
    for c in items:
        key = c.category or "unassigned"
        money_in, money_out = sums.get(key, (ZERO, ZERO))
        sums[key] = (money_in + max(c.txn.amount, ZERO), money_out + max(-c.txn.amount, ZERO))
    return sums
