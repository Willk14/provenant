# ADR 0015: Paralegal drafting assistant

Status: Accepted · 2026-11-24

## Context
A paralegal prepares first drafts of notices, amendments and letters from the contract, and a lawyer finishes them. A
model can write fluent wording, including a wrong date, amount or clause number. In a legal document, those are the
errors that do the most harm and are the easiest to miss.

## Decision
- **A person starts every draft.** Someone chooses a notice, amendment or letter and writes what it must do. Nothing
  runs on upload, and a contract has one draft in progress at a time.
- **Figures are proved in code.** A number, date part, number word or currency in a draft must appear in the quotes cited
  by that paragraph or in the person's instructions. Otherwise the tool refuses the draft. Every quote must be located on
  its cited page, as in ADR 0011.
- **Unknown facts stay visibly unknown.** The agent writes `[[what is needed]]` instead of guessing a name, date or
  amount, and lists points for the lawyer. A draft cannot be approved while a placeholder remains.
- **The check has limits, and the interface says so.** Code proves that a figure came from the source. It does not prove
  that the legal effect is right, that a notice is valid or that an uncited sentence is accurate.
- **A person edits and a different person approves.** A reviewer or partner who did not request, edit or upload it may
  approve or return it, and returning needs a reason. An approved draft is locked. Provenant does not send anything.
- **History is append-only.** Each generation, edit, return and approval is an event with the full wording, a hash and
  the person responsible.
- **Readers.** `baseline` builds a free skeleton with placeholders and the closest clause. `claude-api` writes the draft
  through the harness (ADR 0014) and appears in the usage ledger. `claude` (Managed Agents) does not draft, because its
  hosted agent has a fixed tool list.

## Not in this slice
- Word export and redlines, firm templates and clause banks, and jurisdiction-specific notice rules.
- Measuring draft quality. A paid drafting evaluation needs answer keys reviewed by a qualified lawyer.

Output is a working draft for a qualified lawyer, not legal advice.
