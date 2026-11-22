"""The working-papers pack: one Excel workbook for a client year, with every result Provenant holds for it.

Each sheet is built from the same view the app shows (no second calculation), and every row keeps its source:
a trial balance line, a purchase ledger row, a statement row, a ledger entry, or the person who decided it. A part
that cannot be produced yet (no upload, or a computation waiting on the mapping) is listed on the Index sheet
with the reason, rather than leaving the pack unavailable.
"""
from collections.abc import Callable
from datetime import date, datetime, timezone

from openpyxl import Workbook
from sqlalchemy.orm import Session

from provenant.analytics import journal
from provenant.api import etims, ledger, memo, statements, tax
from provenant.api import export as workbook_export
from provenant.api import variance as checks_api
from provenant.api.auth import Principal
from provenant.api.services import Conflict
from provenant.api.storage import BlobStore
from provenant.tax.engine import KENYA


def workbook(s: Session, p: Principal, store: BlobStore, client_id: str, year_end: date,
             tax_view: Callable[[], dict], tax_sheets_args: Callable[[], tuple]) -> bytes:
    """`tax_view` returns the computation as the Tax tab shows it (raising Conflict while it is blocked);
    `tax_sheets_args` the lead schedule and profit sources for the tax sheets."""
    client = tax.get_client(s, p.tenant_id, client_id)
    wb = Workbook()
    index = wb.active
    index.title = "Index"
    index.append(["Working papers", f"{client.name}, year ended {year_end:%d %b %Y}"])
    index.append(["Prepared by Provenant for", p.user])
    index.append(["Generated (UTC)", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")])
    index.append(["Tax rules", KENYA.version])
    index.append(["Journal entry tests", journal.TEST_VERSION])
    index.append(["Note", "Working papers for review, not tax advice. Amounts are as recorded at generation time."])
    index.append([])
    index.append(["Section", "Status", "Sheets"])
    sections: list[tuple[str, str, str]] = []

    try:
        computation = tax_view()
    except Conflict as exc:
        sections.append(("Tax computation", f"Not produced: {exc}", ""))
    else:
        approval = computation.get("approval")
        if approval is None:
            state = "Not approved by a reviewer"
        elif computation.get("changed_since_approval"):
            state = f"Approved by {approval['reviewer']}, but changed since: needs approving again"
        else:
            state = f"Approved by {approval['reviewer']} on {approval['approved_at'][:10]}"
        before = len(wb.worksheets)
        workbook_export.add_tax_sheets(wb, computation, *tax_sheets_args())
        sections.append(("Tax computation", state, ", ".join(ws.title for ws in wb.worksheets[before:])))

    sections.append(_etims(wb, s, p.tenant_id, client_id, year_end))
    sections.append(_checks(wb, s, p.tenant_id, client_id, year_end))
    sections.append(_statements(wb, s, p.tenant_id, client_id, year_end))
    sections.append(_journal(wb, s, store, p.tenant_id, client_id, year_end))
    sections.append(_memo(wb, s, store, p.tenant_id, client_id, year_end))
    for row in sections:
        index.append(list(row))
    return workbook_export.finish(wb)


def _etims(wb: Workbook, s: Session, tenant_id: str, client_id: str, year_end: date) -> tuple[str, str, str]:
    out = etims._out(etims.run(s, tenant_id, client_id, year_end))
    if out.ledger is None or out.export is None:
        return "eTIMS reconciliation", "Not produced: the purchase ledger and the eTIMS export are both needed", ""
    ws = wb.create_sheet("eTIMS")
    ws.append(["Date", "Supplier", "Supplier PIN", "Invoice", "Account", "Amount", "Status", "eTIMS CU invoice",
               "eTIMS amount", "Unsupported", "In add-back", "Decided by", "Note", "Source"])
    for line in out.lines:
        ws.append([line.date, line.supplier, line.supplier_pin, line.invoice_number, line.account_code, line.amount,
                   line.status, line.cu_invoice_number or "", line.etims_amount, line.unsupported,
                   "yes" if line.in_addback else "no", line.decided_by, line.note,
                   f"{out.ledger.filename} row {line.row_number}"])
    for invoice in out.etims_only:
        ws.append([invoice.date, invoice.supplier_name, invoice.supplier_pin, invoice.invoice_number, "",
                   None, "in eTIMS only", invoice.cu_invoice_number, invoice.taxable_value, None, "no", "", "",
                   f"{out.export.filename}"])
    addback = out.totals.get("addback", 0)
    applied = (f"applied to the computation by {out.applied_by}" if out.applied_by
               else "not applied to the computation")
    return "eTIMS reconciliation", f"Add-back {addback:,.2f}, {applied}", "eTIMS"


def _checks(wb: Workbook, s: Session, tenant_id: str, client_id: str, year_end: date) -> tuple[str, str, str]:
    checks = [checks_api._out(c) for c in checks_api.checks(s, tenant_id, client_id, year_end)]
    done = [c for c in checks if c.filename]
    if not done:
        return "Compliance checks", "Not produced: no VAT, PAYE or WHT return uploaded", ""
    ws = wb.create_sheet("Checks")
    ws.append(["Check", "Line", "Books", "Declared", "Variance", "Tolerance", "Result", "Explanation",
               "Explained by", "Book accounts", "Source"])
    open_items = 0
    for c in done:
        if c.needs_explanation and not c.explanation:
            result, open_items = "To be explained", open_items + 1
        elif c.needs_explanation:
            result = "Explained"
        else:
            result = "Agrees within tolerance"
        ws.append([c.title, "Total", c.book, c.declared, c.variance, c.tolerance, result, c.explanation or "",
                   c.explained_by or "", ", ".join(c.book_accounts), c.filename])
        for r in c.rows:
            ws.append([c.title, r.label, r.book, r.declared, r.variance, None, "", "", "", "", c.filename])
        for note in c.notes:
            ws.append([c.title, "Note", None, None, None, None, note, "", "", "", c.filename])
    missing = len(checks) - len(done)
    status = f"{len(done)} run, {open_items} to be explained" + (f", {missing} not uploaded" if missing else "")
    return "Compliance checks", status, "Checks"


def _statements(wb: Workbook, s: Session, tenant_id: str, client_id: str, year_end: date) -> tuple[str, str, str]:
    out = statements._out(s, tenant_id, client_id, year_end)
    if not out.statements:
        return "Bank and M-Pesa statements", "Not produced: no statement uploaded", ""
    ws = wb.create_sheet("Statements")
    ws.append(["Statement", "Kind", "File", "Uploaded by", "Movements", "Money in", "Money out", "From", "To"])
    for st in out.statements:
        ws.append([st.label, st.kind, st.filename, st.uploaded_by, st.movements, st.money_in, st.money_out,
                   st.first_date, st.last_date])
    ws.append([])
    ws.append(["Category", "", "", "", "", "Money in", "Money out"])
    for category, (money_in, money_out) in out.totals.items():
        ws.append([out.categories.get(category, category), "", "", "", "", money_in, money_out])
    if out.revenue is not None:
        ws.append(["Revenue in the trial balance", "", "", "", "", out.revenue, None])
        ws.append(["Confirmed sales receipts", "", "", "", "", out.confirmed_sales_receipts, None])
    for b in out.breaks:
        ws.append([f"Balance break: {b.source} row {b.row}", "", "", "", "", b.expected, b.stated])
    tx = wb.create_sheet("Statement Lines")
    tx.append(["Date", "Reference", "Description", "Counterparty", "Amount", "Category", "Status", "Rule", "Source"])
    for t in out.transactions:
        tx.append([t.date, t.reference, t.description, t.counterparty, t.amount,
                   out.categories.get(t.category, t.category) if t.category else "", t.status, t.rule,
                   f"{t.source} row {t.row}"])
    status = (f"{len(out.statements)} statement(s), {len(out.transactions)} lines, {len(out.breaks)} balance "
              f"break(s), {out.duplicates} duplicate(s)")
    return "Bank and M-Pesa statements", status, "Statements, Statement Lines"


def _journal(wb: Workbook, s: Session, store: BlobStore, tenant_id: str, client_id: str,
             year_end: date) -> tuple[str, str, str]:
    out = ledger._out(*ledger.analysis(s, store, tenant_id, client_id, year_end))
    if not out.uploaded:
        return "Journal entry tests", "Not produced: no general ledger uploaded", ""
    ws = wb.create_sheet("Journal Tests")
    ws.append(["Test", "Applies", "Entries flagged", "Value", "Open in sample", "Description"])
    for t in out.tests:
        ws.append([t.title, "yes" if t.applies else "no", t.count, t.value, t.open, t.description])
    ws.append([])
    ws.append(["Flagged entry", "Test", "Value", "Detail", "Decision", "Reason", "Decided by", "Source"])
    by_key = {t.key: t.title for t in out.tests}
    for t in out.tests:
        for f in t.sample:
            d = f.decision
            ws.append([f.entry_id, by_key[t.key], f.value, f.detail, d.status if d else "open",
                       d.reason if d else "", d.decided_by if d else "", f"{out.filename} entry {f.entry_id}"])
    flagged = sum(t.count for t in out.tests if t.applies)
    still_open = sum(t.open for t in out.tests)
    status = f"{out.lines:,} lines in {out.entries:,} entries; {flagged} flagged, {still_open} open in the samples"
    return "Journal entry tests", status, "Journal Tests"


def _memo(wb: Workbook, s: Session, store: BlobStore, tenant_id: str, client_id: str,
          year_end: date) -> tuple[str, str, str]:
    out = memo._out(memo.view(s, tenant_id, client_id, year_end, store))
    if out.blocked:
        return "Board tax memo", f"Not produced: {out.blocked}", ""
    ws = wb.create_sheet("Memo")
    ws.append(["Board tax memo", "Field", "Source"])
    for block in out.blocks:
        if block.table:
            for label, value, _ in block.rows:
                ws.append([f"{label}: {value}", block.table, f"table:{block.table}"])
        else:
            text = "".join(seg.text for seg in block.segments)
            fields = sorted({seg.field for seg in block.segments if seg.field})
            ws.append([text, ", ".join(fields), ""])
    ws.append([])
    ws.append(["Field", "Value", "Source"])
    for f in out.fields:
        ws.append([f.label, f.text, f.source])
    author = f"edited by {out.updated_by}" if out.updated_by else "default wording"
    return "Board tax memo", f"Rendered ({author}), {len(out.warnings)} warning(s)", "Memo"
