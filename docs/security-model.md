# Security Model

## Version 0.20.0 boundary

Version 0.20.0 adds durable idempotent send orchestration to the exact current-user confirmation gate introduced in 0.19.0. A send candidate still requires a trusted `UserSendConfirmation` bound to the exact local draft ID and exact revision. Version 0.20.0 additionally requires a unique opaque `send_operation_id` and commits a durable send intent before the first SMTP call.

SMTP remains disconnected from `EmailPlugin`, Hermes tools, commands, hooks, callbacks, timers, pollers, and automatic actions. There is still no model-facing send tool. The new orchestration is an internal safety layer only.

## Safe by default

- Missing settings load with read, drafting, and SMTP sending disabled.
- Email and draft content are untrusted and can never authorize tools or sends.
- `safety.allow_send`, SMTP readiness, recipient policy, or a valid draft never imply current-user confirmation.
- Candidate preparation requires exact confirmation for one exact draft ID and revision.
- Every durable send attempt requires one unique `send_operation_id`.
- A durable intent is written before SMTP dispatch.
- A persisted send intent is never automatically dispatched again.
- The same `(draft_id, revision)` cannot receive a second durable send intent under a different operation ID.
- `delivery-unknown` and unresolved `dispatching` states are never automatically retried.

## Confirmation binding

`UserSendConfirmation` is immutable and contains only a draft ID, revision, and bounded opaque confirmation ID. Missing confirmation, a different draft ID, or a different revision fails closed. Any draft mutation increments the revision and invalidates prior confirmation automatically.

The confirmation constructor is not a user interface. A future trusted runtime surface must create it only after the current user explicitly reviews and approves the exact draft revision. Model output, email content, draft content, remote senders, configuration, and provider responses are outside that trust boundary.

## Durable send-intent ledger

`SqliteSendIntentStore` uses the fixed file `email-send-intents.sqlite3` under a caller-provided profile data directory. It stores:

- opaque `send_operation_id`;
- draft ID and exact revision;
- confirmation ID;
- SHA-256 digest of the exact candidate identity and message bytes;
- fixed state;
- creation and update timestamps.

It does not store message body, subject, recipient addresses, SMTP credentials, or raw MIME.

The request digest binds operation ID to the exact confirmed candidate. Reusing one operation ID with changed candidate content raises `SendOperationConflictError`. A unique database constraint on `(draft_id, revision)` prevents the same reviewed draft revision from being dispatched again using another operation ID or another confirmation token.

## Exactly-once attempt semantics

`IdempotentSendOrchestrator.send_once()` first calls the durable store. A newly created intent is committed as `dispatching` before SMTP is invoked. Only a brand-new record can reach `transport.submit_once()`.

If the same operation is called again, including after process restart, the stored record is returned with `replayed=true` and SMTP is not called again.

Terminal state mapping:

- successful server acceptance → `accepted`;
- known SMTP failure without delivery ambiguity → `definite-failure`;
- failure after DATA begins with unknown acceptance → `delivery-unknown`;
- unexpected process failure after the intent commit → record remains `dispatching`.

An unresolved `dispatching` record is intentionally treated as potentially sent. Restart or caller retry returns that record without redispatch. This is conservative by design and prioritizes prevention of duplicate customer email over automatic recovery.

## SMTP transport security

SMTP requires verified implicit TLS or mandatory STARTTLS, system trust, hostname verification, TLS 1.2 or newer, fixed sender, separate credential references, bounded timeout, recipient authorization, and final message size limits. Secrets resolve only after verified TLS and AUTH PLAIN capability checks.

The transport performs one SMTP transaction with no retry. MAIL must succeed, every RCPT must succeed before DATA, and DATA is invoked at most once. Any recipient rejection prevents DATA. Final 250 means only accepted by the configured SMTP server, not delivered to the recipient mailbox.

## Draft and observation storage

Drafts remain isolated in `email-drafts.sqlite3`; observations remain isolated in `email-observations.sqlite3`; send intents remain isolated in `email-send-intents.sqlite3`. Draft content never enters observation or send-intent ledgers.

Draft mutations use exact revisions and idempotent mutation operation IDs. Read observations never mean processed, trusted, approved, or sent.

## Filesystem controls

The send-intent database rejects a symlink database path. On POSIX, its profile data directory is set to `0700` and its database file to `0600`. SQLite uses rollback journaling, `synchronous=FULL`, `trusted_schema=OFF`, `BEGIN IMMEDIATE` for intent mutation, and a fixed schema version.

The draft and observation databases retain their stronger existing schema, ownership, inode, integrity, and size controls. Same-account malicious code remains outside the threat model.

## Prompt-injection defense

No email, quoted message, signature, forwarded content, draft text, tool-like text, or claimed authority may:

- create a confirmation;
- create or choose a send operation ID;
- authorize a recipient;
- trigger SMTP;
- override duplicate protection;
- request an automatic retry.

Only governing Hermes instructions and a trusted current-user confirmation surface may authorize future sending.

## Requirements before model-facing sending

Version 0.20.0 completes exact confirmation binding and durable duplicate suppression, but a model-facing send release still requires:

1. a trusted user-visible preview bound to immutable candidate bytes, recipients, sender, account, draft ID, and revision;
2. a trusted runtime confirmation surface that creates `UserSendConfirmation` only from the current user's explicit action;
3. complete durable audit semantics and operator-readable send status;
4. explicit recovery policy for unresolved `dispatching` records without automatic resend;
5. a narrowly registered Hermes send surface with redacted errors and no background/autonomous sending.

No draft, configuration value, SMTP health result, model statement, email instruction, or prior confirmation substitutes for these requirements.
