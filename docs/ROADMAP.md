# Roadmap

Built as thin vertical slices. Each milestone ships something a user can run end to end.

## M0 — Deterministic core (this commit)
- [x] Money primitives, balanced journal entries
- [x] Provenance model: `SourceRef`, `ExtractedField`, versioned confidence gate, hash-chained review log
- [x] IFRS 16: lease liability (arrears / advance), amortisation schedule, ROU asset, journal entries
- [x] Golden tests for the maths

## M1 — First lease, end to end
- [ ] FastAPI service, Postgres schema (documents, fields, reviews, entries) with RLS
- [ ] Upload PDF → store by sha256 → extraction job (pluggable `Extractor`) → fields with bbox
- [ ] Review queue API: approve / correct / reject, written to the append-only log
- [ ] Generate schedule + journal entries from verified fields; export to Excel

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
