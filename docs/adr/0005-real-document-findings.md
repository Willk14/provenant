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
   *Done (2026-11-19):* a field either cites a location (`SourceRef`) or is a preparer input about the document
   (`DocumentRef`, stored as page 0) that must state its basis (`POST /documents/{id}/inputs`). Only terms in
   `INPUT_FIELDS` (the discount rate, for leases and amendments) may be entered this way. Review, maker-checker
   and the hash-chained review log are unchanged, and the lease workbook's Provenance sheet shows the basis.
   A rate the document does state can still be extracted and cited.
2. **Rent is a schedule, not a number.** All four amendments escalate annually, and two have rent-free
   months. One lease quotes rent per square foot on an area not yet fixed. The engine needs a payment
   schedule (amount per period), with free periods.
   *Done (2026-11-18):* leases and amendments take `rent_steps` ("13: 1,030.00", or a date for rent tables)
   and `free_periods` ("1, 2" or "2025-04"). The core turns dates into periods. Present value, schedule,
   journal and remeasurement run on the per-period rent; an amendment's new rent replaces the lease's
   steps from its effective date. The real golden set now labels the amendments' rent tables. Rent per
   square foot on an unfixed area is still not representable.
3. **Commencement is often an event**, with an outside date ("no later than May 31, 2022"). The schema needs
   "not yet known" as a state, and a confirmation document to fill it later.
4. **An amendment's IFRS 16 effective date is when the parties agree**, which is usually the signing date,
   not when the new rent starts. Kura signed in May 2025 for an extension starting September 2025. The
   current model conflates the two and refuses extensions that start at the end of the term. It needs both
   dates.
   *Done (2026-11-19):* `effective_date` is the agreed date (IFRS 16 effective date) and the remeasurement
   uses the revised rate and payments from then; `terms_start_date` (optional, defaults to the agreed date)
   is when the new rent or extended term begins, and the old rent runs until then. New terms may start at
   the end of the current term (the Kura case), not after it. Shortcut: a mid-period agreement is
   remeasured at the next period boundary, and the schedule shows agreed, remeasured and new-terms dates.
5. **Amendments state a new expiry date, not a total term from the original commencement.** The schema should
   take the new expiry date.
   *Done (2026-11-19):* `expiry_date`, read as either the last day of a period or the boundary itself; if a
   total term is also given, the two must agree. The real golden amendments are relabelled with all three
   dates from their own text.
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
