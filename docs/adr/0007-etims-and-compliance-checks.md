# ADR 0007: eTIMS reconciliation and compliance checks

Status: Accepted · 2026-11-20

## Context
Kenyan tax compliance is now enforced from electronic records. Expenditure must be supported by an electronic
tax invoice (eTIMS) to be deductible, with exemptions, and KRA matches what clients claim against what
suppliers report. Before a desk audit a consultant wants every purchase posted to a deductible account matched
to an eTIMS invoice or explained, and the rest quantified. Done by hand, it takes days of vouching.

## Decision
- **Inputs are files the client already has**, per client-year, replaced on re-upload: the purchase ledger
  (date, supplier, supplier PIN, invoice number, account code, amount excluding VAT, optional control unit
  number) and the eTIMS purchase invoices export (control unit number, supplier PIN, invoice number, date,
  taxable value). PINs are checked for shape; control unit numbers must be unique.
- **Matching is deterministic and ordered** (`provenant/tax/etims.py`): a person's decision first; then the
  control unit number recorded in the ledger; then supplier PIN and normalised invoice number; then, only as
  *probable*, supplier PIN and amount (within 1.00) dated within seven days when exactly one invoice fits.
  Each invoice matches at most one purchase. A match whose amount or PIN differs is *differs*, not matched.
- **People decide, with reasons.** A purchase can be marked exempt (with a reason, e.g. salaries, bank charges,
  imported services) or matched to a named invoice. Decisions are keyed by supplier PIN, invoice number and
  amount, so they survive a re-upload of the same ledger.
- **The add-back is proposed, then applied.** Unsupported amounts are the whole purchase when unmatched and the
  excess over the invoice when it differs, counted only for purchases posted to accounts the firm has mapped as
  deductible (a fine posted to a disallowable account is already added back). The preparer applies it to the
  computation; it cannot be applied while probable matches are undecided, and any new upload or decision takes
  it out again until re-applied. In the computation it is one line whose sources name each ledger row.
- **Statute stays a preparer confirmation.** The basis text names the Finance Act 2023 amendment and says the
  preparer confirms the provision and exemptions for the year; the rules version moved to `ke-2026.4`.
- **Invoices in eTIMS but not in the ledger** are listed: unrecorded purchases, or invoices issued to the client
  in error.

## Not in this slice
- Sales-side eTIMS and VAT return reconciliation (output VAT), and input VAT claims against eTIMS.
- Reading eTIMS or iTax directly (files only); ERP connectors.
- Cross-return variance checks (VAT turnover vs revenue, PAYE vs staff costs, WHT certificates vs payments):
  next.
