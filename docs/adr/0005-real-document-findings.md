# ADR 0005: What real documents taught us (EDGAR golden set, round 1)

Status: Accepted · 2026-11-17

## Context
Everything up to M3 was built and tested on documents generated in-house, with tidy labels. Before adding
features, eight public EDGAR exhibits were hand-labelled (`backend/golden/real/manifest.json`, with notes on
every judgement call): three credit agreements (IMXI/KeyBank 2024, Ceridian 2021, Dayforce 2024), one office
lease (Recursion 2022) and four lease amendments (Kura, Tenaya, NeuroOne extensions; Bicara expansion).

## Findings

### Extraction
- The deterministic label extractor found **0 of the 21 values the documents state** (after the fix below:
  1 of 21, with 0 invented). Real contracts are prose and defined terms, not "Label: value" lines.
  On the synthetic set it still finds 15 of 29 with 100% precision, which shows how little synthetic
  results say about real documents.
- It also **invented a value that the gate would have auto-accepted**: "INSERT MONTHLY RENT 2. Acceptance of
  Premises" in a blank certificate form became a monthly rent of 2 at 97% confidence. Fixed (a colon or a
  currency marker is now required), but the lesson stands: **confidence must be calibrated per extractor on
  real documents before anything is auto-accepted.**

### The term schemas, not just the extractors, need to change
1. **Leases never state the discount rate.** The incremental borrowing rate is the lessee's own estimate.
   It should be an input entered and signed off by the preparer (with its basis), not an extracted term.
2. **Rent is a schedule, not a number.** All four amendments escalate annually, and two have rent-free
   months. One lease quotes rent per square foot on an area not yet fixed. The engine needs a payment
   schedule (amount per period), with free periods.
3. **Commencement is often an event**, with an outside date ("no later than May 31, 2022"). The schema needs
   "not yet known" as a state, and a confirmation document to fill it later.
4. **An amendment's IFRS 16 effective date is when the parties agree**, which is usually the signing date,
   not when the new rent starts. Kura signed in May 2025 for an extension starting September 2025. The
   current model conflates the two and refuses extensions that start at the end of the term. It needs both
   dates.
5. **Amendments state a new expiry date, not a total term from the original commencement.** The schema should
   take the new expiry date.
6. **Expansions add space at their own rent.** Bicara's figure is for the expansion space only, and under
   IFRS 16.44 added space at market rent can be a separate lease. The model needs "expansion" as its own
   kind of change.
7. **Covenants are frequently springing.** Both Ceridian and Dayforce have a single first-lien leverage
   test (7.25x) that applies only when revolver usage exceeds 35%. The engine tests total net leverage
   unconditionally.
8. **Cure periods are business days, measured from delivery or due date.** IMXI: 15 business days after
   delivery. Dayforce: 15 business days after the due date. Ceridian: 20 calendar days. The engine assumes
   calendar days after the due date.
9. **Test start dates are often relative** ("the first full fiscal quarter ending after the Effective
   Date"). Step-ups (IMXI: 3.75x during an acquisition period) need a schedule of thresholds.
10. **Most EDGAR "lease" exhibits are amendments.** Seven of the ten picked by search were amendments.

## Decision
- Keep the real golden set as the yardstick for every extractor change, scored on recall (stated values
  found) and precision (proposals that are right). Precision decides what may be auto-accepted.
- Treat findings 1-9 as the backlog ahead of new features: they change the term schemas and the engines,
  and any feature built on the old schemas would inherit the gaps.
