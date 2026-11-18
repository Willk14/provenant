"""Fixed asset register: a CSV of assets, summarised per tax class for a year (additions and disposal proceeds).

Columns: asset_code, description, class, acquired, cost; optional disposed, proceeds. If disposed is set,
proceeds must be entered (use 0 when no consideration was received). Dates are ISO (YYYY-MM-DD).
The register supplies the year's additions and disposals; the class's rate, method and basis stay preparer inputs.
"""
import csv
import io
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from provenant.core.money import ZERO, to_decimal

MAX_BYTES = 2 * 1024 * 1024
MAX_ASSETS = 20_000
REQUIRED = ("asset_code", "description", "class", "acquired", "cost")


@dataclass(frozen=True)
class Asset:
    number: int  # CSV row number, for error messages
    code: str
    description: str
    asset_class: str
    acquired: date
    cost: Decimal
    disposed: date | None = None
    proceeds: Decimal = ZERO


@dataclass(frozen=True)
class ClassYear:
    """One class's movements for the year, from the register."""

    asset_class: str
    additions: Decimal  # cost of assets acquired in the year
    disposals: Decimal  # proceeds from assets disposed of in the year
    cost_held: Decimal  # cost of assets still held at the year end
    assets_added: tuple[str, ...]
    assets_disposed: tuple[str, ...]
    assets_held: int


def _date(value: str, what: str, number: int) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"row {number}: {what} must be a date like 2026-03-31, not {value!r}") from exc


def read(data: bytes) -> list[Asset]:
    if len(data) > MAX_BYTES:
        raise ValueError("fixed asset register exceeds 2 MB")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("fixed asset register must be a UTF-8 CSV file") from exc
    reader = csv.DictReader(io.StringIO(text))
    headers = {h.strip().lower() for h in reader.fieldnames or []}
    missing = [c for c in REQUIRED if c not in headers]
    if missing:
        raise ValueError(f"columns needed: {', '.join(REQUIRED)} (optional: disposed, proceeds); missing {', '.join(missing)}")

    assets: list[Asset] = []
    codes: set[str] = set()
    for number, raw in enumerate(reader, start=2):
        row = {k.strip().lower(): (v or "").strip() for k, v in raw.items() if k}
        if not any(row.values()):
            continue
        if len(assets) >= MAX_ASSETS:
            raise ValueError(f"register has more than {MAX_ASSETS} assets")
        code = row["asset_code"][:64]
        if not code:
            raise ValueError(f"row {number}: asset code is empty")
        if code in codes:
            raise ValueError(f"row {number}: asset code {code!r} appears twice")
        codes.add(code)
        asset_class = " ".join(row["class"].split())[:100]
        if not asset_class:
            raise ValueError(f"row {number}: class is empty")
        try:
            if row.get("disposed") and not row.get("proceeds"):
                raise ValueError("proceeds are required when an asset is disposed (enter 0 if no consideration was received)")
            cost = to_decimal(row["cost"])
            proceeds = to_decimal(row["proceeds"]) if row.get("proceeds") else ZERO
        except (ValueError, TypeError) as exc:
            raise ValueError(f"row {number}: {exc}") from exc
        if cost <= 0:
            raise ValueError(f"row {number}: cost must be positive")
        if proceeds < 0:
            raise ValueError(f"row {number}: proceeds cannot be negative")
        acquired = _date(row["acquired"], "acquired", number)
        disposed = _date(row["disposed"], "disposed", number) if row.get("disposed") else None
        if disposed and disposed < acquired:
            raise ValueError(f"row {number}: disposed before it was acquired")
        if proceeds and not disposed:
            raise ValueError(f"row {number}: proceeds given but no disposal date")
        assets.append(Asset(number, code, row["description"][:255], asset_class, acquired, cost, disposed, proceeds))
    if not assets:
        raise ValueError("the register has no assets")
    return assets


def summarise(assets: list[Asset], year_start: date, year_end: date) -> list[ClassYear]:
    """Movements per class for the year from `year_start` to `year_end` inclusive."""
    late = [a for a in assets if a.acquired > year_end]
    if late:
        raise ValueError(f"asset {late[0].code} was acquired after the year end ({late[0].acquired})")
    in_year = lambda d: d is not None and year_start <= d <= year_end  # noqa: E731
    out = []
    for name in sorted({a.asset_class for a in assets}, key=str.lower):
        members = [a for a in assets if a.asset_class == name]
        added = [a for a in members if in_year(a.acquired)]
        disposed = [a for a in members if in_year(a.disposed)]
        held = [a for a in members if a.disposed is None or a.disposed > year_end]
        out.append(ClassYear(
            asset_class=name,
            additions=sum((a.cost for a in added), ZERO),
            disposals=sum((a.proceeds for a in disposed), ZERO),
            cost_held=sum((a.cost for a in held), ZERO),
            assets_added=tuple(a.code for a in added),
            assets_disposed=tuple(a.code for a in disposed),
            assets_held=len(held),
        ))
    return out
