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
- [x] Next.js app: documents list + upload, review queue, document workspace
- [x] pdf.js viewer with every extracted term boxed on the page, coloured by status
- [x] Review in place (approve / correct / reject) with role and maker-checker hints; the server stays the authority
- [x] Mark a missing term by dragging a box on the PDF; the value must match the term's format
- [x] Schedule where any figure traces to the contract terms behind it, highlighted on the PDF
- [x] Server-side value formats (`LeaseTerms.FORMATS`): no "120,000120,000", no rate without "%"
- [x] Amendment chains: amendments link to their lease by id + SHA-256, apply in effective-date order, remeasure the liability and ROU asset (ADR 0003)
- [x] Schedule shows each remeasurement; any figure traces to the exact fields across the lease and its amendments
- [ ] Mid-period modifications, partial terminations, rent deferrals (refused today)
- [ ] OIDC sign-in in the web app (it uses the dev persona switcher today)
- [ ] Component tests (React Testing Library) and a Playwright smoke test in CI

## Schema fixes from real documents (ADR 0005), before new features
- [x] Rent schedules: escalation steps and rent-free months, by month number or date
- [x] Discount rate as a preparer input with its basis (no page cited), partner-reviewed like any judgement
- [x] Amendments: agreed (IFRS 16 effective) date separate from the date new terms start; new expiry date
- [x] Expansions: the IFRS 16.44 judgement as a partner-reviewed input; a separate lease gets its own schedule
- [ ] Springing covenants, business-day cure periods, relative first test dates, threshold step-ups
- [ ] Run the Claude evaluation on the real set (needs an API key) and calibrate confidence per extractor

## M3 — Covenants and full-population analytics
- [x] Credit agreements as a document kind: leverage, interest cover, liquidity, first test date, certificate
      deadline and cure window extracted with page + box; thresholds need a partner (ADR 0004)
- [x] Quarterly trial balance upload: mapped categories, must balance to the cent, stored as integer cents
- [x] Deterministic covenant engine: LTM from four quarters, exact comparisons, headroom, workings
- [x] Deadlines and alerts: certificate due dates, cure windows, overdue data, near-limit warnings
- [x] Covenants tab: results by quarter, workings with the trial balances used, clause highlighted on the PDF
- [ ] Frozen-GAAP / IFRS 16 adjustments using Provenant's own lease schedules; EBITDA add-backs
- [ ] Baskets, DSCR and other covenant types; year-to-date trial balances; Claude extraction of covenant terms
- [ ] GL import → Parquet → DuckDB: Benford, duplicates, weekend/round-sum JE tests

## Tax consultancy and financial mapping (ADR 0006)
- [x] Clients as first-class records; documents (leases) linked to a client
- [x] Year-end trial balance per client; keyword-suggested FS line and tax treatment, confirmed by a person,
      append-only mapping history, carried forward by account code; lead schedule
- [x] Kenya corporation tax: add-backs, exempt income, capital deductions (preparer rates with a basis),
      losses brought forward, IFRS 16 lease crossover from verified schedules, deferred tax (IAS 12)
- [x] Clients and tax UI: mapping table, computation with every line traced to accounts, inputs or leases
- [ ] Reviewer sign-off on mappings and computations (maker-checker, as for extracted terms)
- [x] Records & inputs: prior-year return and roll forward, fixed asset register (additions, disposals,
      balancing charges), instalment / withholding / overpayment credits to tax due, supporting documents
      as evidence
- [x] Per-asset opening tax residual values and disposal outcomes when a matching fixed asset register is used
- [x] Per-class business cessation input, item-level balancing results, and reviewer-approved computation
- [x] Instalment tax planner: lower of current-year estimate and 110% prior-year assessment, withholding reduction,
      agricultural proportions, accounting-period due dates, dated payment allocation, and balance deadline
- [x] eTIMS purchase reconciliation (ADR 0007): ledger vs eTIMS invoices, decisions with reasons, applied add-back
- [x] Variance checks: VAT turnover vs revenue, PAYE vs staff costs, WHT certificates vs payments
- [x] M-Pesa and bank statement parsers: balance breaks, duplicates, own-account transfers, categories
- [x] Board tax memo with every figure bound to the computation (ADR 0008)
- [ ] Minimum tax and interest limits
- [ ] Export the computation and lead schedules to Excel; iTax return figures
- [x] Suggestions learned from the firm's confirmed mappings at other clients (two-thirds agreement, no ties)

## M4 — Due diligence and ERP
- [ ] Data-room bulk ingestion; change-of-control / assignment / indemnity flags
- [ ] ERP connectors (Xero, QuickBooks first) + AI-suggested chart-of-accounts mapping
