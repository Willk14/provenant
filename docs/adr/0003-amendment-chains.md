# ADR 0003: Amendment chains and lease modifications

Status: Accepted · 2026-11-17

## Context
Leases rarely stay as signed. Extensions, rent reviews and side letters arrive as separate documents, often
uploaded out of order. If a superseded rent or term feeds a calculation, every figure after that date is
wrong, and nobody can see why.

## Decision
- **Structure.** A document is a `lease` or an `amendment`. An amendment points at its lease by id and by the
  lease file's SHA-256 (enforced by a check constraint). Amendments always attach to the lease, never to
  each other. Order comes from verified effective dates, not upload order.
- **Terms.** An amendment carries an effective date and a revised discount rate (both required, both partner
  sign-off), plus a new monthly payment and/or a new total term. A term it does not mention carries over.
  Rejecting a proposed change records that the amendment does not change that term.
- **Accounting.** Each amendment is a modification that is not a separate lease (IFRS 16.44-46). At the
  effective date the remaining payments are re-priced at the revised rate, and the difference adjusts the
  right-of-use asset. A decrease larger than the asset's carrying amount goes to profit or loss (IFRS 16.39).
  Depreciation is re-spread over the remaining term.
- **Blocking.** The lease's schedule is refused (409, naming the document) while any amendment is unread or
  unverified, if two take effect on the same date, or if an effective date is not on a period boundary.
- **Evidence.** Every period records which document governed it. Every figure traces to the exact fields
  behind it, across documents. The schedule carries a chain fingerprint (SHA-256 over the lease hash and
  each amendment's hash and effective date), and the Excel export includes it on a Chain sheet.

## Not supported yet (refused, never guessed)
- Mid-period dates for new terms. (Since ADR 0005 #4 an amendment's agreed date may fall mid-period: it is
  remeasured at the first period boundary on or after it, and the schedule shows both dates.)
- Decreases in scope or partial terminations (IFRS 16.46(a)), and early terminations.
- Period-specific rent deferrals and concessions; changes apply uniformly from the effective date.
- Modifications that are separate leases (IFRS 16.44); a new lease is uploaded as its own lease.
