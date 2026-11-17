# Roadmap

Built as thin vertical slices. Each milestone ships something a user can run end to end.

## M0 — Deterministic core (this commit)
- [x] Money primitives, balanced journal entries
- [x] Provenance model: `SourceRef`, `ExtractedField`, versioned confidence gate, hash-chained review log
- [x] IFRS 16: lease liability (arrears / advance), amortisation schedule, ROU asset, journal entries
- [x] Golden tests for the maths

## M1 — First lease, end to end
- [x] FastAPI service; schema for documents, fields and review events, tenant-scoped (SQLite for dev)
- [x] Postgres hardening script: RLS per tenant, append-only trigger on `review_events` (written, not yet run against Postgres)
- [x] Upload PDF → store by sha256 (idempotent) → extraction job (pluggable `Extractor`) → fields with bbox
- [x] Baseline `LeaseLabelExtractor` (deterministic, labelled leases) as fallback and benchmark
- [x] Review queue API: approve / correct / reject, persisted as a hash chain; `/review-log/verify`
- [x] Schedule + journal entries from verified fields only; Excel export with a Provenance sheet

## M1.5 — Before anyone else uses it
- [ ] Real auth (OIDC/JWT) replacing dev headers; roles (preparer, reviewer, partner)
- [ ] Alembic migrations replacing `create_all` + hand-applied SQL; CI runs tests on Postgres
- [ ] Outbox + worker for extraction; LLM extractor behind the `Extractor` port, scored on a golden set
- [ ] Manual field entry that still requires a source location (page + box)

## M2 — The workspace
- [ ] Next.js app: PDF viewer with bbox highlight, click any schedule number → jump to source
- [ ] Amendment chain: link addenda/side letters, remeasure on modification

## M3 — Covenants and full-population analytics
- [ ] Credit agreement extraction: financial covenants, baskets, cure periods, reporting dates
- [ ] Covenant tester against trial-balance data, with alerts before deadlines
- [ ] GL import → Parquet → DuckDB: Benford, duplicates, weekend/round-sum JE tests

## M4 — Due diligence and ERP
- [ ] Data-room bulk ingestion; change-of-control / assignment / indemnity flags
- [ ] ERP connectors (Xero, QuickBooks first) + AI-suggested chart-of-accounts mapping
