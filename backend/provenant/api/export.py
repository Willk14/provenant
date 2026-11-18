"""Excel export an auditor can re-perform: schedule, journal, the document chain, and the provenance of every input."""
import io
from decimal import Decimal

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


def tax_workbook(computation: dict, lead_schedule: list[tuple[str, object, list[str]]],
                 profit_sources: list[str]) -> bytes:
    """Export a tax computation and its supporting lead, allowance and credit schedules."""
    wb = Workbook()
    report = wb.active
    report.title = "Analysis"
    report.append(["Tax analysis", "Amount / finding", "Basis", "Sources"])
    due = Decimal(str(computation["tax_due"]))
    position = "tax refundable" if due < 0 else "tax due"
    report.append(["Tax position", abs(due), position, "tax payable less tax credits"])
    report.append(["Profit before tax", computation["profit_before_tax"], "Trial balance profit or loss lines",
                   ", ".join(profit_sources)])
    report.append(["Tax payable", computation["tax_payable"],
                   f"Taxable profit × {Decimal(str(computation['rate'])) * 100}% tax rate",
                   f"taxable_profit; Rules:{computation['rules_version']}"])
    adjustments = sorted(computation["adjustments"], key=lambda line: abs(Decimal(str(line["amount"]))), reverse=True)
    for rank, line in enumerate(adjustments[:5], start=1):
        report.append([f"Adjustment driver {rank}: {line['label']}", line["amount"], line["basis"],
                       ", ".join(line["sources"])])
    report.append(["Losses used", computation["losses_used"],
                   f"{computation['losses_carried_forward']} carried forward after this year",
                   "tax_inputs.losses_brought_forward"])
    credits_total = sum((Decimal(str(line["amount"])) for line in computation["credits"]), Decimal("0"))
    report.append(["Credits applied", abs(credits_total),
                   f"{len(computation['credit_items'])} recorded credit item(s)",
                   ", ".join(source for line in computation["credits"] for source in line["sources"])])
    for warning in computation["warnings"]:
        report.append(["Review warning", warning, "Computation warning", "tax computation"])

    ws = wb.create_sheet("Computation")
    ws.title = "Computation"
    ws.append(["Tax computation", "Amount", "Basis", "Sources"])
    ws.append(["Rules version", computation["rules_version"], "", ""])
    ws.append(["Profit before tax", computation["profit_before_tax"], "", ", ".join(profit_sources)])
    for line in computation["adjustments"]:
        ws.append([line["label"], line["amount"], line["basis"], ", ".join(line["sources"])])
    summaries = (
        ("Taxable profit before losses", "taxable_before_losses", "profit_before_tax and adjustments"),
        ("Losses brought forward", "losses_brought_forward", "tax_inputs.losses_brought_forward"),
        ("Losses used", "losses_used", "losses brought forward, limited to taxable profit before losses"),
        ("Losses carried forward", "losses_carried_forward", "losses brought forward less losses used"),
        ("Taxable profit", "taxable_profit", "taxable profit before losses less losses used"),
        ("Tax rate", "rate", f"Rules:{computation['rules_version']}"),
        ("Tax payable", "tax_payable", "taxable profit multiplied by tax rate"),
        ("Deferred tax", "deferred_tax", "deferred tax schedule lines below"),
        ("Tax due / (refundable)", "tax_due", "tax payable less tax credits"),
    )
    for label, key, source in summaries:
        ws.append([label, computation[key], "", source])
    for line in computation["deferred"]:
        ws.append([f"Deferred: {line['label']}", line["amount"], line["basis"], ", ".join(line["sources"])])

    lead = wb.create_sheet("Lead Schedule")
    lead.append(["Financial statement line", "Amount", "Account codes"])
    for fs_line, amount, codes in lead_schedule:
        lead.append([fs_line, amount, ", ".join(codes)])

    allowances = wb.create_sheet("Capital Allowances")
    allowances.append(["Class", "Opening WDV", "Additions", "Disposals", "Rate", "Method", "Cost basis",
                       "Allowance", "Disposal gain", "Balancing charge", "Balancing deduction", "Business ceased",
                       "WDV carried forward", "Basis", "Source"])
    for item in computation["allowances"]:
        allowances.append([item["name"], item["wdv_brought_forward"], item["additions"], item["disposals"],
                           item["rate"], item["method"], item["cost_basis"], item["allowance"],
                           item["disposal_gain"], item["balancing_charge"], item["balancing_deduction"],
                           item["business_ceased"], item["wdv_carried_forward"], item["basis"],
                           f"allowance:{item['name']}; " +
                           ("fixed asset register" if item["from_register"] else "preparer input")])

    asset_tax = wb.create_sheet("Asset Tax Detail")
    asset_tax.append(["Asset code", "Opening tax residual value", "Current-year addition", "Capital deduction",
                      "Sale proceeds / deemed market value", "Disposal / cessation result", "Closing tax residual value", "Rate basis",
                      "Disposal basis", "Source"])
    for allowance in computation["allowances"]:
        for item in allowance.get("asset_schedule", []):
            asset_tax.append([item["code"], item["opening_wdv"], item["additions"], item["allowance"],
                              item["proceeds"], item["disposal_result"], item["closing_wdv"], item["rate_basis"],
                              item["disposal_basis"], item["source"]])

    credits = wb.create_sheet("Credits")
    credits.append(["Credit", "Amount", "Reference", "Evidence", "Source"])
    items = computation["credit_items"]
    for line in computation["credits"]:
        for source in line["sources"]:
            try:
                item = items[int(source.removeprefix("credit:"))]
            except (ValueError, IndexError):
                continue
            credits.append([item["description"], item["amount"], item["reference"],
                            item.get("evidence_filename") or item.get("evidence") or "", source])

    for sheet in wb.worksheets:
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for column in sheet.columns:
            letter = column[0].column_letter
            sheet.column_dimensions[letter].width = min(55, max(12, max(len(str(c.value or "")) for c in column) + 2))
        for row in sheet.iter_rows():
            for cell in row:
                if isinstance(cell.value, (int, float)) or hasattr(cell.value, "as_tuple"):
                    cell.number_format = MONEY
                elif isinstance(cell.value, str) and cell.value[:1] in ("=", "+", "-", "@"):
                    cell.value = "'" + cell.value
    for row in ws.iter_rows(min_row=1):
        if row[0].value == "Tax rate":
            row[1].number_format = "0.0%"
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
