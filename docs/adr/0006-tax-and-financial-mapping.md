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

## Not in this slice
- Instalment tax, withholding tax credits, minimum tax, turnover tax, VAT and transfer pricing.
- Claude-suggested mappings (deterministic keyword suggestions only).
- iTax return line mapping and filing exports.
- Partner sign-off of the computation (mappings are confirmed; computation review follows).

All outputs are a professional's working papers, not tax advice. Rates and treatments must be confirmed
against the law in force for the year.
