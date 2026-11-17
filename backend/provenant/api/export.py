"""Excel export an auditor can re-perform: schedule, journal and the provenance of every input."""
import io

from openpyxl import Workbook

from provenant.api.db import FieldRow
from provenant.leases import LeaseSchedule, journal_entries

MONEY = "#,##0.00"


def schedule_workbook(schedule: LeaseSchedule, fields: list[FieldRow]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Schedule"
    ws.append(["Lease liability at commencement", schedule.initial_liability])
    ws.append(["Right-of-use asset at commencement", schedule.initial_rou_asset])
    ws.append([])
    ws.append(["Period", "Period end", "Opening liability", "Interest", "Payment", "Closing liability", "Depreciation"])
    for p in schedule.periods:
        ws.append([p.number, p.end_date, p.opening, p.interest, p.payment, p.closing, p.depreciation])

    journal = wb.create_sheet("Journal")
    journal.append(["Date", "Memo", "Account", "Debit", "Credit"])
    for entry in journal_entries(schedule):
        for line in entry.lines:
            journal.append([entry.on, entry.memo, line.account, line.debit or None, line.credit or None])

    provenance = wb.create_sheet("Provenance")
    provenance.append(
        ["Field", "Value", "Status", "Confidence", "Gate", "Extractor", "Document SHA-256", "Page", "BBox (x0, top, x1, bottom)"]
    )
    for f in fields:
        provenance.append(
            [f.name, f.value, f.status, f.confidence, f.gate_version, f.extractor, f.document_sha256, f.page,
             f"{f.x0}, {f.top}, {f.x1}, {f.bottom}"]
        )

    ws["B1"].number_format = ws["B2"].number_format = MONEY
    for sheet, columns in ((ws, "CDEFG"), (journal, "DE")):
        for column in columns:
            for cell in sheet[column]:
                cell.number_format = MONEY
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
