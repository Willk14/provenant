# Architecture

## Problems this design answers

| Gap in today's tools (Caseware and others) | Provenant's answer |
|---|---|
| Desktop files that lock up, SmartSync conflicts, slow on large files | Browser-native app: Postgres is the source of truth, nothing to sync |
| Analytics in a separate paid desktop tool (IDEA) with no PDF parsing | DuckDB (server) / DuckDB-WASM (browser) over Apache Arrow for full-population GL analytics in the same workspace |
| AI extraction you can't trust or trace | Every value carries a `SourceRef` (document hash, page, bbox, char offsets) |
| LLMs doing maths | The deterministic core does all calculation; AI output is only an *input proposal* |
| Static, bloated checklists | Explicit, stateful workflow graphs with human checkpoints |
| Manual TB/GL wrangling | ERP connectors → canonical schema → AI-suggested CoA mapping (human-confirmed) |

## Layers

```
┌───────────────────────────── Web app (Next.js) ─────────────────────────────┐
│ PDF viewer with bbox highlights · grid workpapers · review queue · DuckDB-WASM │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ REST / OpenAPI
┌──────────────────────────────── API (FastAPI) ───────────────────────────────┐
│ authN/Z (tenant + engagement scoped) · Policy layer · idempotency keys         │
├──────────────────────┬──────────────────────────────┬────────────────────────┤
│ Ingestion            │ Workflow orchestration         │ Deterministic core     │
│ PDF → OCR/VLM →      │ explicit state graph per       │ money · ledger ·       │
│ typed JSON + bbox    │ workflow (lease, covenant, DD) │ IFRS 16 · covenants    │
│ ERP connectors       │ HITL checkpoints               │ (pure Python, Decimal) │
├──────────────────────┴──────────────────────────────┴────────────────────────┤
│ Postgres (OLTP, RLS per tenant, append-only audit) · object storage (PDFs by  │
│ sha256) · DuckDB/Parquet (OLAP) · queue/outbox for async extraction jobs        │
└───────────────────────────────────────────────────────────────────────────────┘
```

## Invariants (enforced in code, not convention)

1. **No floats for money.** `Decimal` everywhere; rounding happens once, at the presentation/posting boundary, with residuals absorbed in the final period.
2. **Every journal entry balances.** `JournalEntry` refuses to construct if debits ≠ credits.
3. **The core only accepts verified facts.** `LeaseTerms.from_fields` raises unless each field was auto-accepted by the gate or approved by a human.
4. **Every fact is traceable.** An `ExtractedField` cannot exist without a `SourceRef`.
5. **The review log is append-only and tamper-evident.** Each entry hashes the previous one; in Postgres the app role has no UPDATE/DELETE grant and a trigger rejects them anyway.
6. **The gate is versioned.** A decision records which threshold version made it, so results are reproducible.
7. **Tenants are isolated twice.** Every query filters by tenant, and Postgres RLS enforces it for the app role.
8. **AI values must be grounded.** A model's value is kept only if its quote is found on the cited page (ADR 0002).
9. **Maker-checker.** Nobody reviews a document they uploaded or a value they entered.
10. **Amendments apply in order.** (Planned) addenda link to their parent by document hash and are applied by effective date; a superseded schedule can never feed a calculation.

## Why this stack

- **Python + FastAPI** for the backend: the strongest ecosystem for document AI and finance maths, and the same stack as KodiLedger, so patterns (outbox, RLS, idempotency) carry over.
- **Postgres** for OLTP + row-level security for tenant isolation; **DuckDB** for OLAP. Two engines, each doing what it is good at.
- **Next.js** front end with an embedded PDF viewer (pdf.js) so evidence never has to be downloaded.
- **Extraction is a port, not a vendor.** An `Extractor` interface lets us swap OCR/VLM providers and run golden-set evaluations against each.
