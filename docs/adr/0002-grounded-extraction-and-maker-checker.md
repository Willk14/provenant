# ADR 0002: Grounded AI extraction and maker-checker review

Status: Accepted · 2026-11-17

## Context
An LLM can read lease prose that patterns cannot (the baseline extractor scores 0 on prose leases in the
golden set). It can also invent plausible numbers. Audit standards also expect segregation of duties:
the person who prepares a figure should not be the one who approves it.

## Decision
- The model reads page text that we extracted ourselves and must return, for each value, the page and an
  exact quote. We locate the quote in that page's text (whitespace-tolerant only). If it is not there,
  the value is dropped. A located quote becomes the field's page, bounding box and character range.
- Claude values get an uncalibrated confidence (0.9) below the gate threshold (0.95), so every one is
  reviewed by a person until the golden set justifies a calibrated score.
- Lease text is treated as data. Injected instructions can at most produce a value that is printed in the
  document, which still needs human approval.
- Reviews require the reviewer or partner role. Judgement fields (discount rate, payment timing) need a
  partner. Nobody can review a document they uploaded or a value they typed in.

## Consequences
- Extraction quality is measurable (`scripts/evaluate_extractor.py`) and any model change can be compared
  against the same golden set and the deterministic baseline.
- Small firms with one person need two accounts to complete a lease. That is deliberate.
