# ADR 0001: Deterministic core behind a probabilistic extraction layer

Status: Accepted · 2026-11-17

## Context
Users of audit and legal tools will not trust figures produced by an LLM, and regulators (SOX 404,
ISA 500) require evidence that can be re-performed. LLMs are good at reading contracts and bad at
reproducible arithmetic.

## Decision
- AI components only *propose* field values. Each proposal carries a confidence score and a `SourceRef`.
- A versioned confidence gate (threshold τ plus a list of always-review fields) routes each proposal
  to auto-accept or human review.
- All calculation and posting is pure, deterministic Python using `Decimal`. The core's entry points
  accept only verified fields.

## Consequences
- Same inputs always produce the same schedule, so outputs are re-performable and testable with golden files.
- Changing τ or the model is a versioned event; historical decisions stay explainable.
- More review work early on; we lower it by calibrating τ against a labelled validation set, not by guessing.
