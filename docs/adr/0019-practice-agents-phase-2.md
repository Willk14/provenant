# ADR 0019: Practice agents phase 2: precedents, docketing and due diligence

Status: Accepted · 2026-11-24

## Context
Phase 1 (ADR 0018) gave firms matters, conflict checks, KYC and the trust account. Phase 2 adds the three agents that
turn a matter into work product and deadlines: drafting from the firm's own precedents, docketing from court notices,
and due diligence across many contracts.

## Decision
- **Precedents are locked and filled, never generated.** A partner locks each precedent as a numbered, append-only
  version. Precedents use `{{field}}` for facts and `[[optional:name]]…[[/optional]]` for clauses chosen per matter. A
  draft is filled from the matter's facts (parties, reference, title, responsible person, date) and any values typed
  in, so the model cannot invent a clause. A missing fact stays as `[[field]]` and blocks approval.
- **Departures are shown, and need a partner.** Any change a person makes to the filled text is compared with the
  precedent paragraph by paragraph. A draft with departures can be approved only by a partner who records why they
  are acceptable. Whoever made or edited a draft cannot approve it.
- **Deadline law is the firm's input.** Each deadline rule (trigger event, days or months, calendar or court days,
  whether it rolls to the next working day) carries the basis a partner enters for it. Court holidays are firm data.
  Provenant counts the days and has hand-worked tests for weekends, holidays, month ends and court days.
- **Notices are read, then confirmed.** The notice reader proposes a case number, the event and dates, with the words
  each came from. Nothing is docketed until a person confirms the event and its date. Confirmed deadlines notify the
  matter's responsible lawyer, and open deadlines export as a calendar file for Outlook or Google Calendar.
- **Due diligence reuses the review agent.** Contracts are linked to a matter's data room and reviewed with the same
  cited-finding agent and playbook (ADR 0011). The matrix shows each contract against each topic, marks high and
  medium findings as deviations, and shows silence as "not found", never as "no issue". It exports to Excel. A contract
  already reviewed is not reviewed or paid for again.

## Not in this slice
- A language-model notice reader. The reader is rules-based; an AI reader can replace it behind the same confirmation.
- Automatic reminders before a deadline, which need a scheduler, and a client portal for milestone updates.
- Word output of approved drafts, and clause-level redlines against the counterparty's paper.

Outputs are working papers for advocates, not legal advice.
