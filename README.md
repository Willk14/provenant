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
cd backend && .venv/Scripts/python scripts/dev.py --reset      # API on :8000 on embedded Postgres (no Docker), migrated, in-process worker, dev auth
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

Deploying: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) (container images, `docker compose up --build` for the whole
stack, staging and production, S3-compatible file storage).

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

Compliance checks (ADR 0007): upload VAT returns, PAYE returns and WHT certificates
(`POST .../checks/returns/{vat|paye|wht}`); `GET .../checks` compares each with the books, and variances above
tolerance take an explanation (`PUT .../checks/{key}/explanation`).

Statements (ADR 0007): upload M-Pesa or bank statement CSVs (`POST .../statements`, with `kind` and `label`);
`GET .../statements` returns balance breaks, duplicates, own-account transfers and categorised movements;
categories are confirmed with `PUT .../statements/categories` or `POST .../statements/confirm-suggestions`.

Board tax memo (ADR 0008): `GET .../memo` renders the memo from the live computation, checks and approval;
`PUT .../memo` saves the text, with `{{field}}` placeholders and `{{table:...}}` blocks (empty text restores the
standard memo). Amounts typed by hand are flagged.

General ledger (ADR 0009): `POST .../ledger` (CSV, optional `approval_limit`) loads the year's ledger into
Parquet; `GET .../ledger` runs the journal entry tests over the whole population with DuckDB and reconciles profit
or loss accounts to the trial balance; `PUT .../ledger/exceptions` clears (a reviewer who did not upload it) or
escalates a flagged entry; `GET .../ledger/entries/{id}` returns its lines.

Every upload that takes a CSV also takes the same table as an Excel workbook (.xlsx, first sheet; ADR 0010).

Working papers: `GET .../papers.xlsx` returns one workbook for the client year: an Index (who, when, rules and
test versions, and the state of each part), the tax computation and its schedules, eTIMS, compliance checks,
statements, journal entry tests and the board memo, each row with its source. Parts that cannot be produced yet
(nothing uploaded, or the computation waiting on the mapping) are listed on the Index with the reason. The
button is beside the financial year on a client's page.

Suggestions are never final: the computation refuses (409) until every account with a balance has a
confirmed mapping, and confirmed mappings carry forward to the next year by account code. Linked leases
replace IFRS 16 depreciation and interest with rentals paid. Working papers for a tax professional, not
tax advice: see [ADR 0006](docs/adr/0006-tax-and-financial-mapping.md).

The Tax tab also shows a provisional Kenyan instalment-tax plan from the preparer's prior-year assessment,
current-year tax computation, agricultural income proportion and dated instalment payment records. Review
the statutory basis and applicable law for the accounting period; the plan is not a penalty calculation.

### Contract review (ADR 0011)

The Legal review tab reviews a contract and its amendments against the firm's playbook. Every finding quotes the PDF,
every topic is either covered or listed as not found, and a lawyer accepts or rejects each one. Output is a working
paper, not legal advice.

| Step | Call |
|---|---|
| Read or change the firm's positions (partner) | `GET`/`PUT /legal/playbook` |
| Start a review; read it | `POST /documents/{id}/reviews`, `GET .../reviews/{rid}` |
| Accept or reject a finding | `PUT .../reviews/{rid}/findings/{fid}` |
| Ask a cited follow-up question | `POST .../reviews/{rid}/questions` |

Contract search (ADR 0012) stores page-located chunks and embeddings in Postgres with pgvector, then merges vector and
keyword results: `GET`/`POST /legal/index`, `POST /legal/search`. `PROVENANT_EMBEDDINGS=hashing` (default) stays on your
server and matches similar words. `voyage` uses the legal model `voyage-law-2`, needs the `voyage` extra and
`VOYAGE_API_KEY`, and sends contract text to Voyage AI. Postgres needs the `vector` extension; on Supabase, enable it
under Database, Extensions before migrating.

`PROVENANT_LEGAL_AGENT=baseline` (default) is a free keyword scan. For the Claude agent, run
`python scripts/setup_legal_agent.py` once, then set `PROVENANT_LEGAL_AGENT=claude` with the two ids it prints.
`PROVENANT_LEGAL_DELETE_SESSIONS=1` deletes the hosted session after each run. Read the data handling section of the
ADR before sending client contracts.

`PROVENANT_LEGAL_AGENT=claude-api` runs the same review on Provenant's own Messages API loop, with no hosted session or
setup script, and hard limits on time and spend (ADR 0014). Claude runs made through the worker's extraction and
`claude-api` paths are logged to an append-only ledger of usage and estimated cost, never content. Partners read it at
`GET /ai/runs`.

The **Drafting** tab is a paralegal assistant for notices, amendments and letters (ADR 0015). A person asks for a draft.
Its figures must trace to the cited contract wording or the instructions, unknown facts stay as `[[placeholders]]`, and
a different reviewer or partner approves it. Provenant never sends a draft. `baseline` produces a free skeleton, and
`claude-api` writes the wording.

**Sign-in and email (ADR 0017).** `/` is the public landing page and `/login` signs people in. With
`NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` in `frontend/.env.local`, and
`PROVENANT_SUPABASE_URL` on the API (instead of `PROVENANT_DEV_AUTH`), people sign in with a password or an emailed
link, and partners invite staff from the **Team** page. Without them, the login page offers the development
personas. `PROVENANT_SMTP_*` sends work notifications, and `PROVENANT_INBOUND_*` gives each firm an address for
emailing in documents. See `deploy/staging.env.example` and `docs/adr/0017-sign-in-and-email.md`.

**Practice agents for law firms (ADR 0018, Phase 1).** **Matters** opens a matter from an intake form, checks every
party against the firm's other matters and uploaded sanctions or PEP lists, and waits for a reviewer or partner other
than the person who opened it to clear or decline it. The file audit flags missing, unverified or expiring KYC items
and practising certificates. **Trust account** reconciles the bank, the cashbook and every client ledger by rules, and
raises hard alerts for breaks, overdrawn or unallocated ledgers and unrecorded bank items. Each Claude run's prompts and
tool calls are kept in an append-only trace that partners can read (`GET /ai/runs/{id}/trace`; `PROVENANT_AI_TRACE=0`
turns it off).

**Phase 2 (ADR 0019).** Partners lock the firm's **Precedents**, and a matter's drafts are filled from its facts, with
any departure from the precedent shown to a partner before approval. The **Docket** reads a pasted court notice,
proposes the event and dates for a person to confirm, then computes deadlines from the firm's own rules and holidays
and exports them as a calendar file. **Due diligence** links contracts to a matter, reviews them all and shows one
matrix of deviations by topic, with an Excel export.

The **AI agents** tab shows whether Claude is on and has a contract chat (ADR 0016). Answers must quote the contract,
and any figure must appear in the quote or the question. Chats are private to their author.

The scorecard (ADR 0013) measures clauses caught, false alarms, citations, retrieval and planted instructions:
`python scripts/evaluate_review.py` is free, `--check` is the CI regression gate, `--real` adds the draft key on real
agreements, and `--reader claude` or `--embedder voyage` need `--allow-paid`.

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
