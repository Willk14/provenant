# Provenant

> **Every number knows where it came from.**

Provenant is a browser-native workspace for the work that sits **between the contract and the ledger**:
lease accounting (IFRS 16 / ASC 842), debt-covenant monitoring, M&A due diligence and audit evidence
matching. Lawyers and finance teams read the same documents for different reasons. Provenant reads
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
| `docs/` | Architecture, decisions (ADRs) and roadmap |

## Getting started

```bash
cd backend
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"    # macOS/Linux: .venv/bin/python
.venv/Scripts/python -m pytest
.venv/Scripts/python -m uvicorn provenant.api.main:app --reload   # API docs at http://127.0.0.1:8000/docs
```

Local identity is taken from `X-Tenant-Id` and `X-User` headers (`PROVENANT_DEV_AUTH=1`, the default). This is
for local development only; real auth lands in M1.5.

### The first workflow (IFRS 16 lease)

| Step | Call |
|---|---|
| Upload a lease PDF (same file twice returns the same document) | `POST /documents` |
| See extracted terms, each with page + bounding box | `GET /documents/{id}` |
| Fields a human must sign off (low confidence, or judgement calls like the discount rate) | `GET /review-queue` |
| Approve / correct / reject, recorded in a hash-chained log | `POST /fields/{id}/review` |
| Schedule from verified terms only (409 until then) | `GET /documents/{id}/lease-schedule` |
| Excel with Schedule, Journal and Provenance sheets | `GET /documents/{id}/lease-schedule.xlsx` |
| Prove the review log hasn't been edited | `GET /review-log/verify` |

### Postgres

```bash
docker compose up -d
DATABASE_URL=postgresql+psycopg://provenant:provenant_dev@localhost:5433/provenant uvicorn provenant.api.main:app
psql postgresql://provenant:provenant_dev@localhost:5433/provenant -f db/postgres/001_hardening.sql
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and [docs/ROADMAP.md](docs/ROADMAP.md).
