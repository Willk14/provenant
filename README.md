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
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and [docs/ROADMAP.md](docs/ROADMAP.md).
