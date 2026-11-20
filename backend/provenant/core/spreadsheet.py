"""Excel (.xlsx) uploads, turned into the CSV every importer already checks.

QuickBooks, Sage, Tally and most Kenyan SME records export to Excel by default. Rather than a second parser per
importer, the first worksheet becomes CSV text and goes through the same validation as an uploaded CSV.

Cell values: Excel stores numbers as binary floating point. Each number becomes text straight away, at the 15
significant digits Excel itself shows ("0.3", never "0.30000000000000004"), and importers parse that text as
Decimal; no float reaches a calculation. Dates become ISO dates (YYYY-MM-DD, with a time only when it has one).
"""
import csv
import io
import zipfile
from collections.abc import Iterator
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

XLSX_MAGIC = b"PK\x03\x04"
MAX_ROWS = 2_000_000
MAX_COLUMNS = 200


class SpreadsheetError(ValueError):
    pass


def is_xlsx(head: bytes) -> bool:
    return head.startswith(XLSX_MAGIC)


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == time(0) else value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        # Excel shows and keeps 15 significant digits; a formula result such as 0.1 + 0.2 is stored as
        # 0.30000000000000004 but displayed, and meant, as 0.3. Never scientific notation.
        text = format(Decimal(format(value, ".15g")), "f")
        return text.rstrip("0").rstrip(".") if "." in text else text
    return str(value).strip()


def _rows(source) -> Iterator[list[str]]:
    try:
        workbook = load_workbook(source, read_only=True, data_only=True)  # data_only: cached results, not formulas
    except (zipfile.BadZipFile, InvalidFileException, KeyError, OSError, ValueError) as exc:
        raise SpreadsheetError("the file is not a readable Excel workbook (.xlsx)") from exc
    try:
        sheet = next((ws for ws in workbook.worksheets if ws.max_row and ws.max_row > 0), None)
        if sheet is None:
            raise SpreadsheetError("the workbook has no worksheet with data")
        for n, row in enumerate(sheet.iter_rows(values_only=True), start=1):
            if n > MAX_ROWS:
                raise SpreadsheetError(f"the worksheet has more than {MAX_ROWS:,} rows")
            cells = [_cell(v) for v in row[:MAX_COLUMNS]]
            while cells and cells[-1] == "":
                cells.pop()
            yield cells
    finally:
        workbook.close()


def _write(rows: Iterator[list[str]], out) -> int:
    writer = csv.writer(out, lineterminator="\n")
    written = width = 0
    pending_blank = 0
    for cells in rows:
        if written == 0 and sum(1 for c in cells if c) < 2:
            continue  # report titles above the table ("Kilima Analytics Ltd", "Trial balance as at ...")
        if not cells:
            pending_blank += 1  # keep blank lines inside the table (row numbers stay right), drop them at the end
            continue
        if written == 0:
            pending_blank, width = 0, len(cells)  # the header sets the width
        for _ in range(pending_blank):
            writer.writerow([""] * width)
        pending_blank = 0
        # Every row as wide as the header: a row whose last cells are empty must not look shorter (CSV readers
        # such as DuckDB's sniff the shape from the rows).
        writer.writerow(cells + [""] * (width - len(cells)))
        written += 1
    if written == 0:
        raise SpreadsheetError("the first worksheet is empty")
    return written


def to_csv(data: bytes, filename: str = "") -> bytes:
    """An upload as CSV bytes: Excel workbooks are converted, CSV passes through, old .xls is refused."""
    if filename.lower().endswith(".xls") and not is_xlsx(data):
        raise SpreadsheetError("old Excel files (.xls) cannot be read: save the sheet as .xlsx or CSV")
    if not is_xlsx(data):
        return data
    out = io.StringIO()
    _write(_rows(io.BytesIO(data)), out)
    return out.getvalue().encode("utf-8")


def file_to_csv(source: Path, target: Path) -> None:
    """Stream a large workbook (a general ledger) to a CSV file without holding it in memory."""
    # An open file, not a path: openpyxl judges a path by its extension, and uploads are saved under neutral names.
    with source.open("rb") as workbook, target.open("w", encoding="utf-8", newline="") as out:
        _write(_rows(workbook), out)
