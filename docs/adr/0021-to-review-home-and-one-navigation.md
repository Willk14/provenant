# ADR 0021: "To review" as the home page, and a short top bar

Status: Accepted · 2026-11-24

## Context
The app had grown two navigations (a top bar of seven sections and a side panel of nine agents) and an AI agents page
that led with the model, the cost and a chat box. Provenant's point (every figure keeps its source; the system
prepares and a person decides) had become hard to see.

## Decision
- **Home is "To review"** (`/home`, `GET /todo`): one list of everything waiting for a person across the firm:
  deadlines due within 14 days or overdue, trial balance mappings to confirm, extracted figures to check, contract
  findings and drafts to decide, conflict checks to clear, court notices to confirm, precedent drafts to approve, trust
  reconciliations to sign off or fix, and missing KYC. Each item links to where the work is, says who prepared it (a
  person or Claude), and is split into "For you" and "Waiting on someone else". Nobody is shown as able to decide what
  they prepared: the reason is given instead ("you opened it", "you prepared it").
- **The top bar holds To review and Documents.** Matters, clients and tax, the trust account and the team stay as
  pages, reached from the items on To review and from each other; the side panel of agents is removed. Agents act
  where the work is: the contract chat is an "Ask Claude" tab on each contract, and the practice agents are buttons in
  matters, drafts and the trust account. The AI agents page, with the engine, usage and cost, is linked from Team.
- Sign-in lands on To review.
