# Provenant

> **Every number knows where it came from.**

Provenant is a browser-native workspace for the work that sits **between the contract and the ledger**:
tax consultancy and financial mapping (Kenya corporate tax first), lease accounting (IFRS 16 / ASC 842),
debt-covenant monitoring, M&A due diligence and audit evidence matching. Lawyers and finance teams read the same documents for different reasons. Provenant reads
them once, links every extracted figure to its exact spot in the source PDF, and passes only
human-verified facts to a deterministic accounting engine.

## The one rule

**AI may extract. Only deterministic code may calculate or post.**

```
 PDF / ERP ──► Extraction (probabilistic) ──► Confidence gate ──► Human review ──► Verified facts
                     │ every value carries a SourceRef        (τ, versioned)    (append-only log)       │
                     ▼                                                                                ▼
              doc sha256 · page · bbox · char offsets                    Deterministic core (Decimal math)
                                                                          IFRS 16 schedules · double-entry ledger
```

## Repository layout

| Path | What it is |
|---|---|
| `backend/provenant/core` | Money and rounding primitives (Decimal only, no floats) |
| `backend/provenant/provenance` | `SourceRef`, extracted fields, the confidence gate and the append-only review log |
| `backend/provenant/ledger` | Balanced double-entry journal entries |
| `backend/provenant/leases` | IFRS 16 lease liability, amortisation schedule, ROU asset and journal entries |
| `backend/provenant/extraction` | `Extractor` port, label baseline, grounded Claude extractor, page text with char positions |
| `backend/provenant/api` | FastAPI app, auth and roles, services, persistence, Excel export |
| `backend/provenant/migrations` | Alembic migrations (0002 = Postgres RLS, append-only log, grants) |
| `backend/provenant/worker.py` | Outbox worker that runs extraction jobs |
| `backend/golden` + `scripts/evaluate_extractor.py` | Golden lease set and extractor scoring |
| `docs/` | Architecture, decisions (ADRs) and roadmap |

## Try the workspace (two terminals)

```bash
cd backend && .venv/Scripts/python scripts/dev.py --reset      # API on :8000, migrated, in-process worker, dev auth
cd backend && .venv/Scripts/python scripts/seed_demo.py         # two sample leases, uploaded as Pat
cd frontend && npm install && npm run dev                        # web app on http://localhost:3100
```

Switch between Pat (preparer), Rae (reviewer) and Pam (partner) in the top bar to see maker-checker at work.
Click a term to find it on the PDF; on a term the extractor missed, choose "Mark on PDF" and drag a box; on the
schedule tab, click any figure to see which contract terms produced it.

| Path | What it is |
|---|---|
| `frontend/src/components/PdfViewer.tsx` | pdf.js pages with term boxes, zoom, and drag-to-mark |
| `frontend/src/components/Workspace.tsx` | Document page: viewer + terms + schedule, polling while extraction runs |
| `frontend/src/lib/trace.ts` | Which contract terms each schedule figure depends on |

## Getting started

```bash
cd backend
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev,postgres]"     # macOS/Linux: .venv/bin/python
.venv/Scripts/python -m pytest
.venv/Scripts/python -m provenant.api.migrate                # create/upgrade the schema
PROVENANT_DEV_AUTH=1 PROVENANT_INLINE_WORKER=1 .venv/Scripts/python -m uvicorn provenant.api.main:app --reload
```

API docs: http://127.0.0.1:8000/docs. With `PROVENANT_DEV_AUTH=1`, identity comes from the `X-Tenant-Id`, `X-User`
and `X-Roles` headers, for local use only. In any shared environment configure OIDC instead:
`PROVENANT_JWT_ISSUER`, `PROVENANT_JWT_AUDIENCE`, `PROVENANT_JWKS_URL` (plus optional `PROVENANT_JWT_TENANT_CLAIM`,
`PROVENANT_JWT_ROLES_CLAIM`). The app refuses to start with neither configured.

`PROVENANT_INLINE_WORKER=1` runs extraction inside the API process for convenience. In production run workers
separately: `python -m provenant.worker`.

### Covenants (credit agreements)

| Step | Call |
|---|---|
| Upload a facility agreement (form field `kind=credit_agreement`) | `POST /documents` |
| Upload a quarterly trial balance CSV (`period_end` = quarter-end) | `POST /documents/{id}/trial-balances` |
| Results per quarter, workings, deadlines and alerts (`as_of` optional) | `GET /documents/{id}/covenants` |

Trial balance CSV: `code,name,category,balance` (debit positive) or `debit,credit` instead of `balance`.
Categories: `cash, borrowings, lease_liabilities, revenue, operating_expenses, depreciation_amortisation,
interest_expense, other`. Each quarter holds that quarter's P&L; it must balance to the cent. See
[ADR 0004](docs/adr/0004-covenant-testing.md) for definitions and what is not covered yet.

### Clients, financial mapping and tax (Kenya)

| Step | Call |
|---|---|
| Create a client | `POST /clients` |
| Upload a year-end trial balance CSV (`year_end`; `code,name,balance` or `debit,credit`) | `POST /clients/{id}/trial-balances` |
| Mapping per account: FS line, tax treatment, suggestion rule, lead schedule | `GET /clients/{id}/years/{ye}/mapping` |
| Confirm one account / all suggestions | `PUT /clients/{id}/mappings/{code}`, `POST .../confirm-suggestions` |
| Preparer inputs: prior-year return, losses, capital allowance classes (rate + basis), tax credits | `PUT .../inputs` |
| Link a verified lease to the client | `PUT /documents/{id}/client` |
| Prior-year roll forward (a proposal: nothing saved) | `POST .../inputs/roll-forward` |
| Fixed asset register CSV (`asset_code,description,class,acquired,cost[,disposed,proceeds]`) | `POST`/`GET .../fixed-assets` |
| Supporting documents (PDF, PNG, JPEG, XLSX, CSV) with a category | `POST`/`GET .../attachments`, `GET /clients/{id}/attachments/{aid}/file` |
| Corporation tax, credits to tax due, and deferred tax; every line traced | `GET /clients/{id}/years/{ye}/tax` |

eTIMS reconciliation (ADR 0007): upload the purchase ledger and the eTIMS purchase invoices export
(`POST .../etims/ledger`, `POST .../etims/invoices`), review matches (`GET .../etims`), decide exemptions or
matches (`PUT .../etims/decisions`) and apply the unsupported total to the computation (`POST .../etims/apply`).

Suggestions are never final: the computation refuses (409) until every account with a balance has a
confirmed mapping, and confirmed mappings carry forward to the next year by account code. Linked leases
replace IFRS 16 depreciation and interest with rentals paid. Working papers for a tax professional, not
tax advice: see [ADR 0006](docs/adr/0006-tax-and-financial-mapping.md).

The Tax tab also shows a provisional Kenyan instalment-tax plan from the preparer's prior-year assessment,
current-year tax computation, agricultural income proportion and dated instalment payment records. Review
the statutory basis and applicable law for the accounting period; the plan is not a penalty calculation.

### Roles

| Role | Can |
|---|---|
| preparer | upload documents, enter missing values (pointing at the page) |
| reviewer | review extracted values |
| partner | review anything, including judgement fields (discount rate, payment timing) |

Nobody can review a document they uploaded or a value they entered (maker-checker).

### Extractors

`PROVENANT_EXTRACTOR=labels` (default, deterministic) or `claude` (needs `ANTHROPIC_API_KEY` or an `ant auth login`
profile). Claude values must quote text found on the cited page or they are dropped, and all of them go to
human review until calibrated. Score either one with `python scripts/evaluate_extractor.py labels|claude`.

### The first workflow (IFRS 16 lease)

| Step | Call |
|---|---|
| Upload a lease PDF (same file twice returns the same document); extraction is queued | `POST /documents` |
| See extracted terms, each with page + bounding box | `GET /documents/{id}` |
| Enter a value the extractor missed, with the box where it is printed | `POST /documents/{id}/fields` |
| Upload an amendment, side letter or extension to a lease (form field `amends=<lease id>`) | `POST /documents` |
| Fields a human must sign off (low confidence, or judgement calls like the discount rate) | `GET /review-queue` |
| Approve / correct / reject, recorded in a hash-chained log | `POST /fields/{id}/review` |
| Schedule from verified terms only, remeasured at each amendment (409 until all are verified) | `GET /documents/{id}/lease-schedule` |
| Excel with Schedule, Journal, Chain and Provenance sheets | `GET /documents/{id}/lease-schedule.xlsx` |
| Prove the review log hasn't been edited | `GET /review-log/verify` |

### Postgres

Migrate as the table owner; run the API and workers as a separate non-superuser role, otherwise row-level
security is bypassed.

```bash
docker compose up -d
psql postgresql://provenant:provenant_dev@localhost:5433/provenant -c "create role provenant_app login password 'change-me'"
PROVENANT_APP_ROLE=provenant_app DATABASE_URL=postgresql+psycopg://provenant:provenant_dev@localhost:5433/provenant python -m provenant.api.migrate
DATABASE_URL=postgresql+psycopg://provenant_app:change-me@localhost:5433/provenant uvicorn provenant.api.main:app
```

Tests against Postgres: `TEST_DATABASE_URL=postgresql://<superuser>@host:port/postgres pytest`. Each test gets
a fresh database, migrated by the superuser, and the app connects as `provenant_app`.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and [docs/ROADMAP.md](docs/ROADMAP.md).
