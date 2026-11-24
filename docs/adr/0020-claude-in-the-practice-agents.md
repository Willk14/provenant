# ADR 0020: Claude in the practice agents

Status: Accepted · 2026-11-24

## Context
The contract agents (review, questions, drafting and due diligence) already run on Claude through Provenant's own
harness (ADR 0014). The practice agents of ADR 0018 and 0019 were rules only. The firm wants every agent to use Claude,
without giving up the rules that hold the design together: a person decides, code does the arithmetic and the dates,
and nothing reaches a client or the court without a human.

## Decision
- **One switch.** The practice agents use Claude whenever the AI agents page shows "Claude is on". They run on the
  harness (`claude-opus-5-5`), with tighter limits than a contract review: 150 seconds, 8 turns and USD 1 a run. Every run
  is in the usage ledger and the trace log.
- **One checked tool.** Each agent gives Claude the material and a single `submit` tool. Provenant checks the answer
  before using it: quotes must appear in the source word for word, ids and numbers must exist, and dates must be real
  dates. A rejected answer goes back with the reasons so Claude can correct it.
- **What Claude does in each agent:**
  - *Docketing:* reads the court notice (case number, event, the event's date, other dates, each with its words). The
    rules engine still counts the deadline, and a person still confirms the event and date.
  - *Conflict checker:* after the spelling match, takes a second look for matches spelling misses (initials, group
    companies, former names). It adds candidates marked "Claude"; it never removes a match the rules found.
  - *Precedent drafting:* suggests values for empty fields from the matter's linked documents, each with the page and
    quote. Values go into the form for a person to check. Claude never writes or changes a clause.
  - *Trust reconciler:* explains unmatched items (bank charges, split payments, mistyped references) on request. The
    difference shown is worked out from the stored amounts, not taken from Claude, and nothing is posted or approved.
  - *File auditor:* drafts the letter asking the client for the missing KYC items, on request. Provenant does not send
    it.
- **Falling back.** When Claude is off, or a run fails or never passes the checks, docketing and conflicts use the rules
  result, and the screen says which reader it was. The on-demand helpers say Claude is off.

## Consequences
Notices and new matters now cost a small amount each when Claude is on, and the text of notices, party names and
unmatched trust items goes to Anthropic. Firms that cannot send these turn Claude off; every agent still works on rules.

Outputs are working papers for advocates, not legal advice.
