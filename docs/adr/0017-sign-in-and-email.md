# ADR 0017: Sign-in, staff directory and email

Status: Accepted · 2026-11-24

## Context
Provenant is becoming a hosted web app. It had an API that accepts OIDC tokens and a development persona switcher, but
no sign-in screen, no way to add staff and no email. Firms want reviewers told when work waits for them, and staff want
to forward contracts from their mailbox.

## Decision
- **Public pages and app pages.** `/` is a landing page and `/login` signs people in. The workspace moves to
  `/documents`. App pages need a signed-in person when Supabase sign-in is configured.
- **Supabase Auth issues the tokens.** People sign in with a password or an emailed link, and the web app sends their
  access token to the API. The API checks it against the project's public keys (`/auth/v1/.well-known/jwks.json`),
  with issuer `<project>/auth/v1` and audience `authenticated`. The firm and roles come from `app_metadata`, which
  only the server can set. `user_metadata` is editable by users and is never used for access.
- **Invitations, not open sign-up.** A partner invites a person from the Team page. The API asks Supabase to email the
  invitation, then sets the firm and roles on that user. The secret key stays on the API. Sign-in links never create
  accounts, and open sign-up should be off in the Supabase project.
- **A directory, not a second source of access.** The `members` table records each person's email, roles and
  notification choice when they sign in or are invited. Access still comes only from the verified token.
- **Notifications are generic.** Emails say that something waits and link to it, such as findings to decide, a draft to
  review, a draft returned or approved, or a run that failed. They never contain contract text, figures, client names
  or file names. They are queued with the work and sent by the worker through the firm's SMTP server. Without one they
  are recorded as skipped. Each person can turn them off.
- **Documents by email go to an inbox.** Each firm has an address `<firm>.<signature>@<inbound domain>`, where the
  signature is an HMAC of the firm id, so no table maps addresses to firms and addresses cannot be guessed. An inbound
  mail service posts the raw message with a shared token. Only attachments from active members are kept, and only
  allowed file types whose bytes match their names. Nothing is read until a person files it, and the person who files
  it becomes its uploader for maker-checker.

## Limits
- The sender check reads the From address, which can be forged. Rely on the mail service's SPF and DKIM checks and
  reject failing mail there, and treat the address as private.
- Firm ids with capital letters get no inbound address, because mail servers may lower-case addresses.
- Filing from the inbox covers contracts. Client records are downloaded and uploaded to the client's year.
- Deleting a Supabase user does not end their current session. Revoke sessions in Supabase when someone leaves.

## Not in this slice
Single sign-on with Microsoft or Google, multi-factor enforcement, digest emails, and reading Outlook or Gmail
mailboxes.
