# ADR 0016: Claude contract chat

Status: Accepted · 2026-11-24

## Context
People want to ask a contract questions in plain language and follow up, without first running a full review. A general
chatbot would answer from memory and could state a wrong clause or figure with confidence.

## Decision
- **One contract per chat.** A person chooses a contract on the AI agents page. The agent can read only that contract and
  its amendments, through the same tools as the review agent. It has no web, file or shell tools.
- **Answers are grounded in code.** An answer must cite words located on the cited page, or say the documents do not
  answer it. Any number, date, number word or currency must appear in the cited words or the person's question.
- **Earlier turns are context, not evidence.** A follow-up can refer to the conversation, but each answer must cite the
  contract again.
- **A person starts every turn.** Nothing runs on upload. A failed turn is recorded and not retried automatically.
- **Chats are private to their author within the firm.** Contract text sent to Claude follows ADR 0014. Messages are
  append-only, and usage goes to the ledger without content.
- **Readers.** `claude-api` and `claude` use Claude. `baseline` returns the closest wording and says it is a keyword scan.

## Not in this slice
- Chat across several contracts or client records, streaming replies, and sharing a chat with a colleague.
- A measured evaluation of chat answers.

Output is a working aid for a qualified person, not legal advice.
