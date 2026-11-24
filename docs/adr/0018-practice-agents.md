# ADR 0018: Practice agents for law firms

Status: Accepted · 2026-11-24

## Context
Law firms asked for agents across intake, drafting, billing, docketing, due diligence, discovery, research and audit.
Some of that work is arithmetic or compliance, where a language model must never be the one deciding. Two red lines
apply to all of it:

1. **No unreviewed external action.** No agent files a pleading, sends advice to a client or moves trust money without
   a person's explicit approval.
2. **No privilege leaks.** Privileged documents go only to an API with an agreement that suits the firm (zero data
   retention where required), and every prompt and tool call is kept in an append-only log.

## Decision
- **The matter is the unit of work.** Matters hold parties, KYC items, conflict checks and trust balances. Every
  decision on a matter is an append-only event with its actor.
- **Deterministic core, agent at the edges.** Conflict matching, KYC completeness, the trust three-way reconciliation,
  limitation periods and billing rules are code with hand-worked tests. Agents read unstructured input, propose and
  draft, and their output waits for a person.
- **Maker-checker everywhere.** The person who opens a matter cannot clear its conflict check, and the person who
  prepares a reconciliation cannot approve it.
- **A full AI trace.** Alongside the content-free usage ledger (ADR 0014), each Claude run's prompts, tool calls and
  tool results are stored in the firm's own database, append-only and tenant-isolated, so a firm can show exactly
  what an agent saw and did.
- **Rules that change are inputs.** Watchlists, required KYC items, dormancy periods and limitation periods are firm
  data with a stated basis, not constants.

## Phases
| Phase | Agents |
|---|---|
| 1 (this slice) | Matters foundation; conflict check and watchlist screening; KYC and file completeness auditor; client trust three-way reconciliation; full AI trace |
| 2 | Template-driven drafting from locked precedents; docketing and deadlines from court notices; due diligence matrix across many contracts |
| 3 | Discovery triage and chronology with privilege flags; citation-verified research; passive timekeeping and pre-bill audit |

Phase 2 and 3 need inputs Provenant does not have yet: a precedent library, court notice formats and limitation
rules entered by the firm, licensed legal research sources, and mailbox or calendar access.

## Not in Phase 1
- WhatsApp or voice intake and automatic sanctions list downloads. A firm uploads its screening list as CSV.
- An AI layer for the trust account. Matching is by amount, date and reference, and the workpaper is generated from
  the reconciliation itself.
- Scheduled background runs. Audits are computed whenever someone opens them.

Outputs are working papers for advocates, not legal advice. Zero data retention is an arrangement with Anthropic that
the firm must put in place. Provenant cannot switch it on.
