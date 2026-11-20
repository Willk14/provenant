"""Every CSV upload also takes the same table as an Excel workbook, with the same checks and results."""
import csv
import io
import re
from datetime import date, datetime
from decimal import Decimal

from openpyxl import Workbook

from tests.test_api import PREPARER
from tests.test_etims import ETIMS, LEDGER
from tests.test_etims_api import setup
from tests.test_journal import GL
from tests.test_statements import MPESA
from tests.test_tax_api import ROWS, client_with_tb, tb_csv
from tests.test_tax_records import REGISTER
from tests.test_variance_api import VAT

D = Decimal
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def as_xlsx(text: str, title: str | None = None) -> bytes:
    """The CSV fixture as an exporter would write it: numbers as numbers, dates as dates, optional title row."""
    wb = Workbook()
    ws = wb.active
    if title:
        ws.append([title])
    for row in csv.reader(io.StringIO(text)):
        cells = []
        for value in row:
            plain = value.replace(",", "")
            if re.fullmatch(r"-?\d+(\.\d+)?", plain) and not re.fullmatch(r"0\d+", plain):
                cells.append(float(plain) if "." in plain else int(plain))
            elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                cells.append(date.fromisoformat(value))
            elif re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}(:\d{2})?", value):
                cells.append(datetime.fromisoformat(value))
            else:
                cells.append(value)
        ws.append(cells)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def post(client, url, data, name="upload.xlsx", **form):
    return client.post(url, headers=PREPARER, data=form, files={"file": (name, data, XLSX)})


def test_tax_records_take_excel(client, app):
    cid, year = setup(client)  # its trial balance came in as CSV; next year's comes in as Excel
    r = post(client, f"/clients/{cid}/trial-balances", as_xlsx(tb_csv(ROWS).decode(), "Trial balance at 31 Dec 2027"),
             year_end="2027-12-31")
    assert r.status_code == 201, r.text
    accounts = client.get(f"/clients/{cid}/years/2027-12-31/mapping", headers=PREPARER).json()["accounts"]
    assert {a["code"]: D(a["balance"]) for a in accounts}["4000"] == D(-1_000_000)

    r = post(client, f"{year}/fixed-assets", as_xlsx(REGISTER.decode()))
    assert r.status_code == 201, r.text
    assert len(r.json()["assets"]) == 4

    r = post(client, f"{year}/etims/ledger", as_xlsx(LEDGER.decode()))
    assert r.status_code == 201, r.text
    r = post(client, f"{year}/etims/invoices", as_xlsx(ETIMS.decode()))
    assert D(r.json()["totals"]["addback"]) >= 0 and len(r.json()["lines"]) == 6

    r = post(client, f"{year}/checks/returns/vat", as_xlsx(VAT))
    vat = {c["key"]: c for c in r.json()}["vat_turnover"]
    assert D(vat["declared"]) == D(990_000)  # same as the CSV upload of these returns

    mpesa = MPESA.decode().split("\n", 2)[2]  # the portal's two summary lines become a title row instead
    r = post(client, f"{year}/statements", as_xlsx(mpesa, "KILIMA ANALYTICS LTD - M-PESA STATEMENT"),
             kind="mpesa", label="M-Pesa Till 123456")
    assert r.status_code == 201, r.text
    assert r.json()["statements"][0]["movements"] == 4

    r = post(client, f"{year}/ledger", as_xlsx(GL))
    assert r.status_code == 201, r.text
    counts = {t["key"]: t["count"] for t in r.json()["tests"]}
    assert (r.json()["lines"], counts["unbalanced"], counts["duplicates"], counts["weekend"]) == (24, 1, 2, 2)


def test_excel_errors_are_plain(client, app):
    cid = client_with_tb(client)
    r = client.post(f"/clients/{cid}/trial-balances", headers=PREPARER, data={"year_end": "2027-12-31"},
                    files={"file": ("tb.xls", b"\xd0\xcf\x11\xe0 old binary excel", "application/vnd.ms-excel")})
    assert r.status_code == 422 and ".xls) cannot be read" in r.json()["detail"]
    unbalanced = as_xlsx("code,name,balance\n1000,Bank,10\n4000,Sales,-9\n")
    r = post(client, f"/clients/{cid}/trial-balances", unbalanced, year_end="2027-12-31")
    assert r.status_code == 422 and "does not balance" in r.json()["detail"]
