"""Reading trial balance CSVs: code, name, and either balance (debit positive) or debit and credit columns.

Strict on purpose: amounts parse exactly (no floats, proper thousands separators, at most two decimals),
every row is reported by its row number, and the result must balance to the cent.
"""
import csv
import io
from dataclasses import dataclass
from decimal import Decimal

from provenant.core.money import ZERO, round_money, to_decimal

MAX_BYTES = 2 * 1024 * 1024
MAX_LINES = 20_000


@dataclass(frozen=True)
class Row:
    number: int  # CSV row number, for error messages
    code: str
    name: str
    balance: Decimal  # debit positive
    category: str | None  # the optional "category" column, lower-cased


def read(data: bytes, require_category: bool = False) -> list[Row]:
    if len(data) > MAX_BYTES:
        raise ValueError("trial balance file exceeds 2 MB")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("trial balance must be a UTF-8 CSV file") from exc
    reader = csv.DictReader(io.StringIO(text))
    headers = {h.strip().lower() for h in reader.fieldnames or []}
    needed = {"code", "name"} | ({"category"} if require_category else set())
    if not needed <= headers or not ({"balance"} <= headers or {"debit", "credit"} <= headers):
        columns = "code, name, " + ("category, " if require_category else "")
        raise ValueError(f"columns needed: {columns}and balance (or debit and credit)")

    rows: list[Row] = []
    codes: set[str] = set()
    for number, raw in enumerate(reader, start=2):  # row 1 is the header
        row = {k.strip().lower(): (v or "").strip() for k, v in raw.items() if k}
        if not any(row.values()):
            continue
        if len(rows) >= MAX_LINES:
            raise ValueError(f"trial balance has more than {MAX_LINES} lines")
        code = row["code"][:64]
        if not code:
            raise ValueError(f"row {number}: account code is empty")
        if code in codes:
            raise ValueError(f"row {number}: account code {code!r} appears twice")
        codes.add(code)
        try:
            if row.get("balance"):
                balance = to_decimal(row["balance"])
            else:
                balance = to_decimal(row.get("debit") or "0") - to_decimal(row.get("credit") or "0")
        except (ValueError, TypeError) as exc:
            raise ValueError(f"row {number}: {exc}") from exc
        if round_money(balance) != balance:
            raise ValueError(f"row {number}: balance has more than two decimal places")
        rows.append(Row(number, code, row["name"][:255], balance, (row.get("category") or "").lower() or None))
    if not rows:
        raise ValueError("trial balance has no lines")
    difference = sum((r.balance for r in rows), ZERO)
    if difference:
        raise ValueError(f"trial balance does not balance (off by {difference})")
    return rows
