"""Excel export an auditor can re-perform: schedule, journal, the document chain, and the provenance of every input."""
import io

from openpyxl import Workbook

from provenant.api.services import ChainedSchedule
from provenant.leases import journal_entries

MONEY = "#,##0.00"


def schedule_workbook(chained: ChainedSchedule) -> bytes:
    schedule = chained.schedule
    documents = [chained.lease, *chained.amendments]
    filename = {d.id: d.filename for d in documents}
    governed_by = [filename[seg.document_id] for seg in chained.segments]

    wb = Workbook()
    ws = wb.active
    ws.title = "Schedule"
    ws.append(["Lease liability at commencement", schedule.initial_liability])
    ws.append(["Right-of-use asset at commencement", schedule.initial_rou_asset])
    ws.append([])
    ws.append(["Period", "Period end", "Opening liability", "Interest", "Payment", "Closing liability", "Depreciation",
               "Terms from"])
    for p in schedule.periods:
        ws.append([p.number, p.end_date, p.opening, p.interest, p.payment, p.closing, p.depreciation,
                   governed_by[p.segment]])

    journal = wb.create_sheet("Journal")
    journal.append(["Date", "Memo", "Account", "Debit", "Credit"])
    for entry in journal_entries(schedule):
        for line in entry.lines:
            journal.append([entry.on, entry.memo, line.account, line.debit or None, line.credit or None])

    chain = wb.create_sheet("Chain")
    chain.append(["Chain fingerprint (SHA-256)", chained.fingerprint])
    chain.append([])
    chain.append(["Order", "Document", "Kind", "SHA-256", "Amends (SHA-256)", "Effective", "After period",
                  "Liability before", "Liability after", "ROU adjustment", "Gain to P&L"])
    chain.append([0, chained.lease.filename, "lease", chained.lease.sha256, None, None, None, None, None, None, None])
    for i, (amendment, r) in enumerate(zip(chained.amendments, schedule.remeasurements), start=1):
        chain.append([i, amendment.filename, "amendment", amendment.sha256, amendment.parent_sha256, r.effective,
                      r.after_period, r.liability_before, r.liability_after, r.rou_adjustment, r.gain])

    provenance = wb.create_sheet("Provenance")
    provenance.append(["Document", "Field", "Value", "Status", "Confidence", "Gate", "Extractor", "Document SHA-256",
                       "Page", "BBox (x0, top, x1, bottom)"])
    for f in chained.fields:
        provenance.append(
            [filename[f.document_id], f.name, f.value, f.status, f.confidence, f.gate_version, f.extractor,
             f.document_sha256, f.page, f"{f.x0}, {f.top}, {f.x1}, {f.bottom}"]
        )

    ws["B1"].number_format = ws["B2"].number_format = MONEY
    for sheet, columns, first_row in ((ws, "CDEFG", 1), (journal, "DE", 1), (chain, "HIJK", 4)):
        for column in columns:
            for cell in sheet[column][first_row - 1:]:
                cell.number_format = MONEY
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
