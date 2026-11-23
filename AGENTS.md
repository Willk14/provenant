# AGENTS.md — working on Provenant

Provenant is a workspace for the work between the contract and the ledger: Kenya corporate tax and
financial mapping, IFRS 16 leases, and debt covenants. Every figure keeps a link to its source (a PDF box,
a trial balance line, a preparer input or a supporting document). Read `README.md`, `docs/ARCHITECTURE.md`
and the ADRs in `docs/adr/` before changing behaviour; ADR 0006 covers the tax work.

## Run and test (Windows paths; on macOS/Linux use `.venv/bin/python`)

```bash
cd backend && .venv/Scripts/python -m pytest -q -p no:warnings                 # SQLite
cd backend && TEST_DATABASE_URL="postgresql://postgres:<pw>@127.0.0.1:<port>/postgres" .venv/Scripts/python -m pytest -q -p no:warnings
cd frontend && npx tsc --noEmit && npx vitest run
cd backend && .venv/Scripts/python scripts/dev.py --reset                       # API on :8000 on embedded Postgres (app role, RLS on), migrated, dev auth, inline worker; --sqlite for the old file database
cd backend && .venv/Scripts/python scripts/evaluate_review.py --check                 # contract review scorecard (free; ADR 0013)
cd backend && .venv/Scripts/python scripts/seed_demo.py                          # demo leases, facility, client Kilima Analytics Ltd
cd frontend && npm run dev                                                       # web app on http://localhost:3100 (proxies /api)
```

Run both the SQLite and the Postgres suites before committing; CI (`.github/workflows/ci.yml`) runs both.
The Postgres suite needs a superuser URL (it creates a database per run and tests row-level security).

## Layout

| Path | What it is |
|---|---|
| `backend/provenant/core/` | `money.py` (strict Decimal parsing), `tb_csv.py` (trial balance CSV), formats |
| `backend/provenant/leases/`, `covenants/` | Pure IFRS 16 and covenant engines |
| `backend/provenant/analytics/` | `journal.py`: general ledger to Parquet and journal entry tests in DuckDB (ADR 0009) |
| `backend/provenant/tax/` | Pure tax code: `mapping.py` (FS lines, treatments, keyword suggestions), `engine.py` (computation, credits, deferred tax), `register.py` (fixed asset register CSV) |
| `backend/provenant/legal/` | Contract review and retrieval: playbook, cited-finding toolbox, agents, chunks, embeddings (ADR 0011, 0012) |
| `backend/provenant/api/` | FastAPI app (`main.py`), models (`db.py`), use cases (`services.py`, `tax.py`), tax HTTP routes (`tax_routes.py`) |
| `backend/provenant/migrations/versions/` | Alembic `0001`–`0017`; tests build the schema through migrations |
| `frontend/src/lib/tax.ts` | Tax API client and types |
| `frontend/src/components/` | `ClientWorkspace`, `MappingTab`, `RecordsTab`, `TaxTab`, plus lease and covenant views |

Deployment: `backend/Dockerfile` (API, worker, migrations), `frontend/Dockerfile`, root `docker-compose.yml`, `deploy/`,
`docs/DEPLOYMENT.md`; the CI `stack` job builds and smoke-tests the whole stack.

## Rules that hold the design together

- **Money is `Decimal`, never float.** Parse with `provenant.core.money.to_decimal`; store integer cents
  (`*_cents` columns). Round only through `round_money`.
- **Engines stay pure and deterministic.** No I/O in `provenant/tax`, `leases`, `covenants`. Each engine test
  states its hand-worked arithmetic in a comment; keep doing that.
- **Nothing suggested is used until a person confirms it.** Mappings are suggested, then confirmed; the
  computation returns 409 while any account with a balance is unconfirmed. Roll forward returns a proposal and
  saves nothing.
- **Changeable tax law is input, not code.** Capital allowance rates and methods are preparer inputs with a
  stated basis. Only settled points go in `Rules` (`KENYA`, versioned `ke-2026.4`); bump the version when
  rules change. Output is working papers, not tax advice.
- **Every computation line names its sources** (`tb line id`, `allowance:<class>`, `credit:<i>`, lease
  document id) so the UI can trace it.
- **Tenancy.** Every table has `tenant_id`; every query filters by it. A new table needs, in its migration,
  RLS (`ENABLE` + `FORCE` + `tenant_isolation` policy) and grants for `PROVENANT_APP_ROLE`, and an isolation
  test in `tests/test_postgres.py`. History tables are append-only (trigger).
- **Maker-checker.** Nobody reviews what they uploaded or entered. Roles: preparer, reviewer, partner.
- **Dev auth** (`PROVENANT_DEV_AUTH=1`) takes identity from `X-Tenant-Id`, `X-User`, `X-Roles`. The frontend adds
  these in `lib/api.ts`, so files are fetched with headers and opened as blobs, not plain links.
- **Uploads** are content-addressed (`BlobStore`), size-limited and type-checked by leading bytes.

## Git

The repository is public. Commit as `Willk14 <Willk14@users.noreply.github.com>` (already set in the repo's
git config); never put a personal email in commits or files. Commit on `main` in small, described commits;
push only when the owner asks.

## Next steps, in priority order

1. ~~Reviewer sign-off on the tax computation~~: done (approval snapshot, "changed since approval").
2. ~~Excel export~~: done. `tax.xlsx` (computation, lead schedule, allowances, asset detail, credits) and the
   working-papers pack `papers.xlsx` (`api/papers.py`): an Index sheet, the tax sheets, eTIMS, compliance checks,
   statements, journal entry tests and the memo, each built from the view the app shows, with a source on every
   row; a part that cannot be produced yet is listed on the Index with the reason. Possible follow-up: iTax
   return figures.
3. ~~Frontend checks in CI~~: done (`frontend` job; component tests for `RecordsTab` and `TaxTab`).
4. ~~Demo data fix~~: done (retained earnings brought forward is a 120M credit, and the seed refuses otherwise).
5. **Tax rules still missing** (ADR 0006 "Not in this slice"): minimum tax, interest restriction / thin
   capitalisation and instalment penalties. Instalment planning and due dates are implemented with a
   preparer-entered prior-year assessment, current-year estimate, agriculture income ratio and dated receipts.
   Business-cessation balancing charges and deductions
   now require a matching register and reviewer-approved computation. The current fixed-asset-register path
   uses per-asset opening residual values and disposal outcomes; class-only
   inputs remain available when no matching register is supplied. Each new rule needs a hand-worked test.
6b. **General ledger analytics (ADR 0009), first slice done** (Excel uploads everywhere: ADR 0010): next are ERP extracts, fuzzy duplicates and
   ledger exceptions in the board memo; DuckDB-WASM in the browser only after the server path is proven.
6a. **Compliance checks (ADR 0007), in progress:** eTIMS purchase reconciliation, variance checks, M-Pesa/bank
   statement parsers and the board tax memo (ADR 0008) are done.
6. ~~Schema fixes from real documents (ADR 0005)~~: done (discount rate input, amendment dates, expansions, springing
   leverage, business-day cure periods, relative first test dates and threshold step-ups). Possible follow-up:
   extracting the last two, and event-triggered step-ups.
7. ~~Mapping suggestions learned from the firm's confirmed mappings~~: done (`learn` / `suggest_for_firm` in
   `tax/mapping.py`). Possible follow-up: a "refresh suggestions" action for accounts still unconfirmed.

Work in this folder (the git repository). A copy elsewhere that is not a git checkout loses files that are
never committed, as happened with the RecordsTab and TaxTab tests.

The owner runs the Claude extraction evaluation (`scripts/evaluate_extractor.py claude --real`) with their own
API key; do not ask for or store keys.
