# ADR 0022: The trial balance mapping agent

Status: Accepted · 2026-11-24

## Context
Mapping each trial balance account to a financial statement line and a tax treatment is the slowest step of the tax
work. Suggestions came from the firm's own confirmed mappings, then fixed keyword rules; anything else stayed
"unmapped" for a person (ADR 0006, AGENTS.md step 7).

## Decision
- **Claude is a third source of suggestions, behind the firm's own practice.** "Map with Claude" on the mapping tab
  sends Claude the accounts that are unmapped or only guessed by a keyword rule, with their names and balances, the
  firm's line list and the tax treatments. It never sees or changes an account a person has confirmed or one the
  firm's practice already maps.
- **Every answer is checked before it is saved.** The account must be one that was sent, the line and treatment must
  exist and agree (`check_mapping`: statement of financial position lines take `balance_sheet` and nothing else), and
  each answer needs a reason. A rejected answer goes back to Claude with the reasons.
- **A suggestion is still only a suggestion.** Claude's answers are saved as `suggested` with the rule
  `claude: <reason>`, shown on the account with a Claude label. The computation still returns 409 until a person
  confirms every account with a balance, and confirmations still go to the mapping history. Accounts Claude cannot
  place are left for a person.
- Runs use the practice agents' limits (ADR 0020) and are recorded in the usage ledger and trace log. Unconfirmed
  mappings appear on To review (ADR 0021).

Output is working papers, not tax advice.
