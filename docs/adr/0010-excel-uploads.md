# ADR 0010: Excel workbooks wherever a CSV is accepted

Status: Accepted · 2026-11-20

## Context
Kenyan SMEs keep records in Excel, QuickBooks, Sage and Tally, and those export Excel by default. Every
Provenant importer took CSV only, so a consultant re-saved each file before uploading: trial balances, fixed
asset registers, purchase ledgers and eTIMS exports, VAT/PAYE/WHT returns, M-Pesa and bank statements and the
general ledger.

## Decision
- **One converter, not a parser per importer** (`provenant/core/spreadsheet.py`). An `.xlsx` upload (recognised by
  its leading bytes, not its name) becomes CSV text of its first worksheet with data, and goes through exactly
  the validation an uploaded CSV gets. Old binary `.xls` is refused with a plain message to save as `.xlsx`.
- **No float reaches a calculation.** Excel stores numbers as binary floating point; each becomes text at the 15
  significant digits Excel itself shows ("0.3", not "0.30000000000000004"), never in scientific notation, and the
  importers parse that text as Decimal. Dates become ISO dates, with a time only when there is one. Formulas
  contribute their cached results.
- **Exports as they come:** report-title rows above the table (fewer than two filled cells) are skipped, every row
  is padded to the header's width, and blank rows inside the table keep row numbers in error messages right.
- **Large ledgers stream:** the general ledger workbook is read row by row (openpyxl read-only) into a CSV file
  for DuckDB, never wholly in memory. The original upload is what is stored and hashed as evidence.
- No new dependency: openpyxl was already used for the lease workbook export.

## Not in this slice
- Choosing a worksheet other than the first, `.xlsb` and `.ods`, and PDF statements.
