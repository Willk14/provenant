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
- [x] OIDC/JWT auth (JWKS, RS256/ES256); dev headers only behind an explicit flag; app refuses to start with neither
- [x] Roles: preparer uploads, reviewer reviews, partner signs off judgement fields; maker-checker on every review
- [x] Alembic migrations; Postgres RLS + append-only trigger + least-privilege grants as migration 0002
- [x] Full suite runs on Postgres as a non-superuser role (RLS really enforced); CI matrix SQLite + Postgres
- [x] Transactional outbox + worker (SKIP LOCKED, retry with backoff, dead-letter, stale-job reclaim)
- [x] Claude extractor: every value must quote text found on the cited page, or it is dropped
- [x] Golden set + evaluation script (baseline: 16/30, 0 wrong)
- [x] Manual field entry that must point at printed text on the page

## M1.6 — Open items
- [ ] Run the Claude extractor on the golden set (needs an API key; costs money) and calibrate its confidence
- [ ] Grow the golden set with real, anonymised leases; OCR for scanned PDFs
- [ ] Object storage (S3/GCS) for PDFs; rate limits and audit logging on the API

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
