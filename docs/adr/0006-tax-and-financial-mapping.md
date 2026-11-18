# ADR 0006: Tax consultancy and financial mapping (Kenya corporate tax first)

Status: Accepted · 2026-11-18

## Context
The product is being oriented towards tax consultancy and financial mapping work. A tax consultant's job
is per client and per year: take the client's trial balance, map every account to the financial
statements and to a tax treatment, compute taxable profit and tax, and defend each adjustment to the
client, the reviewer and the Kenya Revenue Authority (KRA). Today that lives in spreadsheets, where the
mapping is invisible and the adjustments are re-keyed.

## Decision
1. **Clients are first-class.** A tenant (the firm) has clients; trial balances, mappings and tax
   computations belong to a client and a financial year end. Leases can be linked to a client.
2. **Financial mapping is a reviewed artefact.** Each client account (by code) maps to a financial
   statement line and a tax treatment. Mappings are suggested deterministically from account names, then
   confirmed by a person (recorded: who, when). Confirmed mappings carry forward to the client's next
   year by account code. A computation will not run while any account with a balance is unconfirmed.
3. **The computation is deterministic and traced.** Profit before tax, plus add-backs (disallowable
   expenses, accounting depreciation, IFRS 16 depreciation and lease interest), less deductions (capital
   allowances, lease rentals paid, exempt income), less losses brought forward, gives taxable profit and
   tax. Each line lists the accounts or inputs it came from and a basis (e.g. Income Tax Act s.16).
   Deferred tax (IAS 12) is computed on property, plant and equipment (carrying amount vs tax written-down
   value), leases (right-of-use asset vs lease liability) and tax losses.
4. **The lease crossover is used.** Leases linked to a client feed the IFRS 16 tax adjustments for the
   year straight from Provenant's verified lease schedules: rentals paid are deducted, and right-of-use
   depreciation and lease interest are added back.
5. **Statute is configuration, not code, and conservative.** The Kenya rule set carries only what is
   settled and widely known: the 30% resident corporation tax rate and the broad structure of the Income
   Tax Act (s.15 deductions allowed, s.16 deductions not allowed, Second Schedule capital deductions,
   Third Schedule rates). Capital allowance rates and methods are preparer inputs, each with a stated
   basis, because they change with Finance Acts. The rule set is versioned and every computation records
   the version it used.

## Addendum: the records a consultant inputs
- **Prior-year return.** The return's acknowledgement number and filing date, losses brought forward and
  each class's opening written-down value. "Roll forward" proposes these from last year's computation (and
  an overpayment credit if it ended refundable); it saves nothing until a person reviews and saves.
- **Fixed asset register** (CSV per client-year, replaced on re-upload). For a capital deduction class with
  the same name, the register supplies current-year additions and disposal proceeds; typed class movements
  are ignored for that class. Each pre-existing registered item needs a preparer-entered opening tax
  residual value, which is carried forward by asset code from the prior computation. The computation
  calculates the allowance and disposal result item by item and traces each result to the asset code.
  Register classes with no matching deduction class are flagged; the rate and basis are never guessed.
- **Business cessation uses a separate balancing outcome.** A preparer may mark a deduction class as ceased
  only for a year in which the business using the items stopped and every item in the class ceased to be owned.
  The class must match the fixed asset register, all relevant items must have an exit by year-end, and the
  register must state sale consideration or the deemed market value. The item residual value is calculated
  after the year's allowance; paragraph 4's charge or deduction is traced to each asset. Selling a class while
  the business continues remains a paragraph 3 trading receipt or loss. The resulting computation snapshot
  must be approved by a reviewer or partner before it is treated as approved working paper.
- **Tax credits:** instalment tax, withholding tax, overpayment brought forward and other credits, each with
  a reference and optionally the supporting document that evidences it. The computation ends at tax due,
  or refundable when credits exceed the tax charged.
- **Instalment tax planning:** the planner applies Income Tax Act s.12's lower-of method (current-year tax
  estimate or 110% of prior-year assessed tax), reduces the amount by withholding credits, and applies the
  Twelfth Schedule proportions. It uses the agricultural schedule only when the preparer records that more
  than two-thirds of current-year income is agricultural. Dates are calculated from the accounting period,
  dated instalment receipts allocate against due dates, and the balance deadline is shown. The report names
  its basis and source inputs; it does not calculate late-payment penalties. Confirm the applicable law and
  current-year estimate before relying on the schedule.
- **Supporting documents:** PDF, PNG, JPEG, XLSX or CSV per client-year, content-addressed like leases,
  type-checked by their leading bytes, served with `nosniff`. A document that evidences a credit cannot be
  deleted until the credit no longer points at it.

## Not in this slice
- Minimum tax, turnover tax, VAT and transfer pricing; item-level tax schedules for classes that do not have
  a matching fixed asset register.
- Claude-suggested mappings (deterministic keyword suggestions only).
- iTax return line mapping and filing exports.
- Partner sign-off of the computation (mappings are confirmed; computation review follows).

All outputs are a professional's working papers, not tax advice. Rates and treatments must be confirmed
against the law in force for the year.
