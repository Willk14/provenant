# ADR 0004: Financial covenant testing from trial balances

Status: Accepted · 2026-11-17

## Context
Lenders, borrowers' finance teams and their auditors all test the same covenants each quarter, usually in a
spreadsheet that re-keys the thresholds from the facility agreement and the numbers from the trial balance.
Both re-keyings are where errors hide, and a missed breach or a missed cure window is expensive.

## Decision
- **Terms come from the agreement, with provenance.** A credit agreement is a document kind. Its terms are
  extracted like a lease's: maximum leverage, minimum interest cover, minimum liquidity, first test date,
  compliance certificate deadline and equity cure window. Each keeps its page and box, and thresholds plus the
  cure window need a partner's sign-off. Rejecting a proposed covenant records that the agreement has no such
  covenant.
- **Numbers come from trial balances, mapped by the preparer.** A trial balance is a CSV of accounts with a
  category column (cash, borrowings, lease liabilities, revenue, operating expenses, depreciation and
  amortisation, interest expense, other). Upload is refused unless every line has a known category and the
  trial balance balances to the cent. Balances are stored as integer cents, and the file's SHA-256 is kept as
  evidence.
- **The calculation is deterministic.** Last-twelve-months P&L is the sum of four consecutive quarterly trial
  balances. Balance-sheet figures come from the test date. Net debt = borrowings + lease liabilities − cash, and
  EBITDA = revenue − operating expenses. Comparisons use exact values (rounding is for display only), so a
  result exactly on the threshold passes and anything past it breaches. Negative EBITDA is a leverage breach,
  not a negative ratio.
- **Deadlines are first-class.** Every quarterly test date has a certificate due date; a breach with a cure
  right has a cure deadline. Alerts, most urgent first: open cure windows, quarters past their deadline with
  data missing, results within 10% of a limit, certificates due within 45 days.
- **Workings are evidence.** Each result shows its workings, the four trial balances used, and highlights the
  clause it was tested against on the agreement PDF.

## Not supported yet
- Agreement-specific definitions: EBITDA add-backs, pro forma adjustments, and frozen-GAAP treatment of IFRS 16
  leases. Provenant already has the lease schedules needed for frozen GAAP; wiring them in is the next step.
- Year-to-date trial balances (differencing), non-quarterly test periods, and covenants other than the three
  above (DSCR, capex limits, baskets).
- AI-suggested account mapping. The preparer supplies the category column today.
- Claude extraction of covenant terms. The label extractor covers conventional wording.
