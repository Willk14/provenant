# ADR 0011: Contract review agent

Status: Accepted · 2026-11-23

## Context
Lawyers and finance teams read the same contracts for different reasons. A review agent can find the clauses that
matter, but a legal tool that invents a clause or decides a point on its own is worse than none. Provenant already
requires that AI values quote the page they came from and that a person verifies them (ADR 0002).

## Decision
- **The agent has only Provenant's tools.** It can list the contract and its amendments, read a page, search, record
  a finding, finish, or answer a question. Built-in shell, file and web tools are off, and the environment has no
  outbound network. Contract text is marked as data, and an instruction inside it can do no more than produce a
  finding that a person still has to decide.
- **Every citation is proved in code.** A finding or answer must quote words located on the cited page (whitespace
  differences only). Otherwise the tool refuses it and the agent must read the page again.
- **Every playbook topic is accounted for.** A review cannot finish until each topic has a finding or is listed as not
  found, so silence never reads as "no issue". The interface asks a lawyer to confirm what was not found.
- **The playbook is the firm's.** A partner can replace the standard positions. Each review stores the exact positions
  it used and their hash, so later changes do not rewrite past reviews.
- **A person decides.** A reviewer or partner who did not upload the contract accepts or rejects each finding, and a
  rejection needs a reason. Accepting a finding with a proposed term only places that term in normal review, with the
  acceptor as its enterer, so a second person must verify it. A finding never overwrites a live term.
- **Two readers behind one port.** `baseline` is a free, deterministic keyword scan that shows where to read and makes
  no judgement. `claude` runs a Managed Agent created once by `scripts/setup_legal_agent.py`; custom tools execute in
  Provenant's worker, inside the tenant's session.
- **Failures cost once.** A failed review is recorded and not retried automatically. A run stops at eight minutes, under
  the worker's stale-job limit, and at 200 tool calls.

## Data handling
With `claude`, the pages the agent reads go to Anthropic, and Managed Agents keeps session history until it is deleted.
It is a beta service and is not eligible for zero data retention. `PROVENANT_LEGAL_DELETE_SESSIONS=1` deletes each session
when it ends. A firm whose client terms do not allow this should stay on `baseline` until the loop is moved to the
Messages API under suitable terms.

## Not in this slice
- A measured evaluation of the Claude reviewer on real contracts; its findings are unscored until one exists.
- Redlines or Word output, jurisdiction-specific law, external legal research, and OCR for scanned contracts.
- Comparing a contract with a template, bulk data-room review, and the review in the working-papers pack.

Output is a professional's working paper, not legal advice.
