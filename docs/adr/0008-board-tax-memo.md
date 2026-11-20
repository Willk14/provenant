# ADR 0008: Board tax memo bound to the computation

Status: Accepted · 2026-11-20

## Context
A tax computation ends in a memo for the board or the client. In practice the figures are typed into Word from
the workbook, and when an adjustment changes late, someone hunts through the report for every number it
touched. A figure typed by hand is the one that goes stale, and reviewers cannot tell which numbers were checked.

## Decision
- **The memo is text with bound fields.** `{{taxable_profit}}`-style fields and table blocks
  (`{{table:computation}}`, `{{table:deferred}}`, `{{table:checks}}`) render from the live computation every time
  the memo is opened (`provenant/tax/memo.py`). Each rendered figure keeps its source, shown on hover.
- **Fields are a fixed vocabulary** (`FIELD_NAMES`): totals, losses, rate, credits, tax due (in figures and as a
  sentence that says "due" or "refund"), the eTIMS add-back, deferred tax and its kind, approval status, open
  variances and the rules version. Unknown fields are refused on save.
- **Typed amounts are flagged, not blocked.** Numbers with thousands separators, or five digits or more, outside a
  field produce a warning to bind them instead. Years and short references (s.16, 2026) are left alone.
- **The memo reports state, it does not change it:** compliance rows come from the eTIMS reconciliation and the
  variance checks (with explanations quoted), and the status line says whether the computation is approved and
  whether it changed since.
- **A standard memo** is provided; a firm's edited text is stored per client-year, and clearing it returns to the
  standard. Printing uses the browser (print or save as PDF), showing only the memo.

## Not in this slice
- Claude-drafted narrative. When added, Claude writes prose around fields and may never emit a figure: its output
  is checked with the same typed-number rule before it is shown.
- Word (.docx) export and firm letterheads; memo review and sign-off separate from the computation's approval.
