# ADR 0012: Contract chunks, embeddings and hybrid retrieval

Status: Accepted · 2026-11-23

## Context
The review agent (ADR 0011) reads every page, which suits one contract but not a question across many. Similarity
search can also miss the one clause that matters, so it must help find text without becoming the evidence.

## Decision
- **Chunks keep their place.** Pages are split at clause headings, with overlap when a passage is long. A chunk never
  crosses a page and stores its page, character range and box, so every result can be highlighted.
- **pgvector in the existing Postgres.** Embeddings sit beside the data under the same row-level security. There is no
  separate vector service to secure or keep in step. SQLite, used for quick tests, compares vectors in Python.
- **Exact search within the tenant.** An approximate index combined with a tenant or contract filter can return fewer
  matches without saying so. Exact search is the right default at this volume. Add an approximate index only after
  measuring recall at real volume.
- **Hybrid ranking.** Vector and Postgres full-text results are merged with reciprocal rank fusion (k = 60), so a passage
  both methods find ranks above one that only a single method likes.
- **Two embedding providers.** `hashing` (default) is local and free: no text leaves the server, but it matches shared and
  similar words and is not semantic. `voyage` uses `voyage-law-2` and sends contract text to Voyage AI. Vectors are stored
  by model, so changing provider requires reindexing and never mixes them.
- **Retrieval is a lead, not proof.** A full review still reads every page. The agent can use retrieval to locate
  wording, but findings and answers must still quote text proved on the cited page. An empty result is never presented
  as proof that a contract is silent. If indexing fails, a review continues by reading the pages.
- **Indexing is deliberate.** Uploading does not send text anywhere. A review indexes its own contract, and a person can
  index the firm's documents from the search box.

## Not in this slice
- Measured recall on real contracts for either provider. Thresholds are starting values until that exists.
- Reranking, contextual chunk embeddings, OCR, clauses joined across page breaks, and approximate indexes.
- Bulk data-room review. Search across a firm's contracts is the foundation for it.
