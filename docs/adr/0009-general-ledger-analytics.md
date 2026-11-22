# ADR 0009: Full-population journal entry tests over the general ledger

Status: Accepted · 2026-11-20

## Context
Audit and tax work tests journal entries for override and error: entries that do not balance, duplicates, round
sums, postings at odd times or by unusual users, rarely used accounts, period-end entries and entries just
under an approval limit. Desktop tools test a sample or need a separate analytics product. A client's ledger can
run to millions of lines, and the machines the work runs on are ordinary laptops (one in this project had about
400 MB free).

## Decision
- **DuckDB inside the API, over Parquet.** The upload is streamed to disk, read, checked and normalised by
  DuckDB (`provenant/analytics/journal.py`), and written to Parquet; the CSV and the Parquet are both stored by
  their SHA-256. Every test is SQL over the Parquet, with DuckDB capped at 512 MB and two threads. Amounts are
  DECIMAL(18,2) throughout. Measured on 500,000 lines (33 MB CSV): load 1.2 s, all tests 1.7 s, Parquet 10 MB.
- **Server side first, not DuckDB-WASM in the browser.** Results are small (counts, totals and the largest 200
  entries per test); loading millions of rows into a browser tab fails on the machines this targets.
- **Fixed, versioned tests** (`je-2026.1`): unbalanced entries, possible duplicates (same account, amount, date
  and description in different entries), round amounts (100,000 or more in whole tens of thousands), weekend
  postings, after-hours postings (needs `posted_at`), unusual users (at most 2 entries or 1% of the year; needs
  `user`), rarely used accounts (2 lines or fewer), period-end entries (last 5 days, or dated after the year
  end) and entries within 10% below an approval limit the preparer enters. A test that lacks its column says so
  rather than passing. Benford's first-digit test reports observed against expected proportions and Nigrini's
  mean-absolute-deviation conformity; it needs at least 100 lines of 10 or more.
- **A flag selects, it does not judge.** Entries are ranked by how many tests flag them. Each flagged entry can
  be cleared (a reviewer or partner who did not upload the ledger, with a reason) or escalated (anyone, with a
  reason); decisions are kept per entry and test.
- **The ledger is reconciled** to the mapped trial balance for profit or loss accounts, whose year's movement is
  the balance.

## Not in this slice
- ERP extracts; fuzzy near-duplicates; per-user or per-account baselines across years. (Excel uploads: ADR 0010.
  The board memo reports flagged, cleared, escalated and open items: ADR 0008.)
