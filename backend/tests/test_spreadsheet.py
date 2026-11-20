import io
from datetime import date, datetime
from decimal import Decimal

import pytest
from openpyxl import Workbook

from provenant.core import tb_csv
from provenant.core.spreadsheet import SpreadsheetError, _cell, file_to_csv, to_csv


def xlsx(rows, title_rows=0) -> bytes:
    """A workbook as QuickBooks/Sage exports look: optional title rows, then a header and data."""
    wb = Workbook()
    ws = wb.active
    for i in range(title_rows):
        ws.append([f"Kilima Analytics Ltd - report line {i + 1}"] if i == 0 else [])
    for row in rows:
        ws.append(row)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def test_numbers_and_dates_become_exact_text():
    data = xlsx([["code", "name", "balance", "when"],
                 ["1000", "Bank", 1234.56, date(2026, 3, 31)],
                 [4000, "Revenue", -0.3, datetime(2026, 3, 31, 14, 5)],
                 [5000, "Big", 1e7, None]])
    text = to_csv(data, "tb.xlsx").decode()
    assert text.splitlines() == [
        "code,name,balance,when",
        "1000,Bank,1234.56,2026-03-31",
        "4000,Revenue,-0.3,2026-03-31 14:05:00",
        "5000,Big,10000000,",  # padded to the header's width
    ]


def test_a_trial_balance_workbook_goes_through_the_csv_checks():
    data = xlsx([["code", "name", "balance"], ["1000", "Bank", 1500.25], ["4000", "Sales", -1500.25]])
    rows = tb_csv.read(to_csv(data, "tb.xlsx"))
    assert [(r.code, r.balance) for r in rows] == [("1000", Decimal("1500.25")), ("4000", Decimal("-1500.25"))]
    unbalanced = xlsx([["code", "name", "balance"], ["1000", "Bank", 10], ["4000", "Sales", -9]])
    with pytest.raises(ValueError, match="does not balance"):
        tb_csv.read(to_csv(unbalanced, "tb.xlsx"))


def test_csv_passes_through_and_bad_files_are_refused():
    assert to_csv(b"code,name\n1,x\n", "tb.csv") == b"code,name\n1,x\n"
    with pytest.raises(SpreadsheetError, match=r"\.xls\) cannot be read"):
        to_csv(b"\xd0\xcf\x11\xe0 legacy", "tb.xls")
    with pytest.raises(SpreadsheetError, match="not a readable Excel"):
        to_csv(b"PK\x03\x04 not really a zip", "tb.xlsx")
    with pytest.raises(SpreadsheetError, match="empty"):
        to_csv(xlsx([]), "empty.xlsx")


def test_leading_blank_rows_go_and_large_workbooks_stream_to_a_file(tmp_path):
    data = xlsx([[], [], ["entry_id", "date", "account_code", "amount"], ["A", date(2026, 1, 5), "1000", 10]])
    assert to_csv(data, "gl.xlsx").decode().splitlines()[0] == "entry_id,date,account_code,amount"
    source, target = tmp_path / "gl.xlsx", tmp_path / "gl.csv"
    source.write_bytes(data)
    file_to_csv(source, target)
    assert target.read_text(encoding="utf-8").splitlines() == ["entry_id,date,account_code,amount", "A,2026-01-05,1000,10"]


@pytest.mark.parametrize(("value", "text"), [
    (0.1 + 0.2, "0.3"),  # a formula result stored as 0.30000000000000004: Excel shows 0.3
    (1234.5600000000001, "1234.56"),
    (-1500.25, "-1500.25"),
    (1e15, "1000000000000000"),  # no scientific notation
    (120000.0, "120000"),
    (7, "7"),
])
def test_numbers_are_the_15_digits_excel_shows(value, text):
    assert _cell(value) == text


def test_report_titles_above_the_table_are_skipped():
    data = xlsx([["code", "name", "balance"], ["1000", "Bank", 5], ["4000", "Sales", -5]], title_rows=2)
    assert to_csv(data, "tb.xlsx").decode().splitlines()[0] == "code,name,balance"
