# ADR 0014: Claude harness and AI run ledger

Status: Accepted · 2026-11-24

## Context
Provenant calls Claude in two places, written separately: structured lease and credit extraction, and the contract
review agent on Managed Agents (ADR 0011). Neither recorded what a run used or cost. The review's deadline was checked
only when an event arrived, so a quiet stream could outlast it and reach the worker's ten-minute stale-job limit. ADR
0011 also left open moving the review loop to the Messages API for firms whose client terms rule out hosted sessions.

## Decision
- **One loop that Provenant owns.** `provenant/ai/harness.py` runs a tool loop on the Messages API. The model can call
  only the tools it is given, and those tools run in the tenant's worker. Thinking blocks are passed back unchanged.
- **Limits are enforced in code.** Each run has a wall-clock deadline (eight minutes), a limit on silence between stream
  events (one minute, so the worst case stays under the stale-job limit), a turn limit and a spending limit. Transient
  API failures are retried twice within the deadline. A run that fails is recorded and is not retried by the job queue
  logic of ADR 0011.
- **A third reader, chosen by the firm.** `PROVENANT_LEGAL_AGENT=claude-api` uses this loop with the same prompt, tools
  and citation checks as `claude`. `baseline` stays the default and `claude` is unchanged.
- **A ledger of usage, never of content.** Each model run adds one row to the append-only, tenant-isolated `ai_runs`
  table: purpose, document id, models served, outcome, turns, tool calls, token counts, estimated cost and duration. It
  holds no prompt, contract text, tool input or model output. A failure to write the ledger is logged and never fails
  the job, because the model has already been paid for.
- **Cost is an estimate in integer micro-dollars.** Prices are versioned in code. A model with no listed price is
  stored with no cost and counted at a deliberately high rate against the spending limit.
- **Partners see usage.** `GET /ai/runs` returns recent runs and totals for the firm.

## Data handling
With `claude-api`, the pages the agent reads go to Anthropic under the firm's API terms, and no hosted session is
created. Retention depends on those terms. A firm that needs zero data retention must have it arranged with Anthropic
before using this reader.

## Not in this slice
- Moving `claude` runs onto the ledger, or changing the Managed Agents deadline.
- A live run: the loop is tested against the real SDK with a mocked transport, and Claude's review quality is still
  unmeasured until the owner runs the paid scorecard (ADR 0013).
- A usage screen in the web app, firm-wide monthly budgets and the Claude-drafted memo narrative (ADR 0008).

Output remains a professional's working paper, and a person decides every finding.
