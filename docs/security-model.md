# Security Model

## Version 0.21.0 boundary

Version 0.21.0 hardens durable idempotent send orchestration with strict uncertain-delivery recovery. A send candidate still requires a trusted `UserSendConfirmation` bound to the exact local draft ID and exact revision, plus a unique opaque `send_operation_id` persisted before the first SMTP call.

The key change is that unresolved send state can no longer remain ambiguously `dispatching` after a true process restart. Any interrupted dispatch owned by an earlier process is atomically converted to `delivery-unknown`. That state is terminal for automatic behavior and requires manual external verification before any separate corrective action.

SMTP remains disconnected from `EmailPlugin`, Hermes tools, commands, hooks, callbacks, timers, pollers, and automatic actions. There is still no model-facing send tool. The orchestration remains an internal safety layer only.

## Safe by default

- Missing settings load with read, drafting, and SMTP sending disabled.
- Email and draft content are untrusted and can never authorize tools or sends.
- `safety.allow_send`, SMTP readiness, recipient policy, or a valid draft never imply current-user confirmation.
- Candidate preparation requires exact confirmation for one exact draft ID and revision.
- Every durable send attempt requires one unique `send_operation_id`.
- A durable intent is written before SMTP dispatch.
- A persisted send intent is never automatically dispatched again.
- The same `(draft_id, revision)` cannot receive a second durable send intent under a different operation ID.
- `delivery-unknown` is terminal for automatic behavior and requires manual review.
- A prior-process or legacy unresolved `dispatching` record is recovered to `delivery-unknown`, never retried.
- A live same-process `dispatching` record remains live and is not falsely recovered by a concurrent caller.

## Confirmation binding

`UserSendConfirmation` is immutable and contains only a draft ID, revision, and bounded opaque confirmation ID. Missing confirmation, a different draft ID, or a different revision fails closed. Any draft mutation increments the revision and invalidates prior confirmation automatically.

The confirmation constructor is not a user interface. A future trusted runtime surface must create it only after the current user explicitly reviews and approves the exact draft revision. Model output, email content, draft content, remote senders, configuration, and provider responses are outside that trust boundary.

## Durable send-intent ledger

`SqliteSendIntentStore` uses the fixed file `email-send-intents.sqlite3` under a caller-provided profile data directory. Version 0.21.0 schema v2 stores:

- opaque `send_operation_id`;
- draft ID and exact revision;
- confirmation ID;
- SHA-256 digest of the exact candidate identity and message bytes;
- fixed state;
- nullable internal dispatcher ownership;
- creation and update timestamps.

It does not store message body, subject, recipient addresses, SMTP credentials, or raw MIME.

The request digest binds operation ID to the exact confirmed candidate. Reusing one operation ID with changed candidate content raises `SendOperationConflictError`. A unique database constraint on `(draft_id, revision)` prevents the same reviewed draft revision from being dispatched again using another operation ID or another confirmation token.

## Exactly-once attempt semantics

`IdempotentSendOrchestrator.send_once()` first calls the durable store. A newly created intent is committed as `dispatching` with the current process dispatcher ID before SMTP is invoked. Only a brand-new record can reach `transport.submit_once()`.

If the same operation is called again in the same process while the original SMTP call is live, the persisted `dispatching` record is replayed and no second SMTP call occurs. Process-local locks serialize access to the same profile ledger.

If a persisted `dispatching` row belongs to another process, has no dispatcher identity, or comes from the v0.20 schema, recovery converts it atomically to `delivery-unknown`. This avoids both silent resend and indefinite ambiguous state after restart.

Terminal state mapping:

- successful server acceptance → `accepted`;
- known SMTP failure without delivery ambiguity → `definite-failure`;
- failure after DATA begins with unknown acceptance → `delivery-unknown`;
- unexpected exception during a live send → persist `delivery-unknown` before propagation whenever possible;
- interrupted prior-process `dispatching` → recover to `delivery-unknown`.

`delivery-unknown` means the configured SMTP server may already have accepted the message. Automatic retry is forbidden. A human must verify authoritative external state, such as the provider's Sent folder or server evidence, before deciding on any separate corrective action.

Every `SendAttemptRecord` exposes `delivery_is_uncertain`, `automatic_retry_forbidden`, and `manual_review_required` semantics so callers cannot safely treat unknown delivery as ordinary failure.

## Schema migration

The send-intent schema advances from v1 to v2. Migration is additive and preserves all existing terminal records. A nullable `dispatcher_id` column is added when absent, the schema version is advanced, and any unresolved legacy dispatch is recovered as `delivery-unknown` on the next orchestrator/store recovery path.

The migration never recreates or discards the ledger and never replays an old SMTP attempt.

## SMTP transport security

SMTP requires verified implicit TLS or mandatory STARTTLS, system trust, hostname verification, TLS 1.2 or newer, fixed sender, separate credential references, bounded timeout, recipient authorization, and final message size limits. Secrets resolve only after verified TLS and AUTH PLAIN capability checks.

The transport performs one SMTP transaction with no retry. MAIL must succeed, every RCPT must succeed before DATA, and DATA is invoked at most once. Any recipient rejection prevents DATA. Final 250 means only accepted by the configured SMTP server, not delivered to the recipient mailbox.

## Draft and observation storage

Drafts remain isolated in `email-drafts.sqlite3`; observations remain isolated in `email-observations.sqlite3`; send intents remain isolated in `email-send-intents.sqlite3`. Draft content never enters observation or send-intent ledgers.

Draft mutations use exact revisions and idempotent mutation operation IDs. Read observations never mean processed, trusted, approved, or sent.

## Filesystem controls

The send-intent database rejects a symlink database path. On POSIX, its profile data directory is set to `0700` and its database file to `0600`. SQLite uses rollback journaling, `synchronous=FULL`, `trusted_schema=OFF`, `BEGIN IMMEDIATE` for intent mutation, and a monotonic schema version.

The draft and observation databases retain their stronger existing schema, ownership, inode, integrity, and size controls. Same-account malicious code remains outside the threat model.

## Prompt-injection defense

No email, quoted message, signature, forwarded content, draft text, tool-like text, or claimed authority may:

- create a confirmation;
- create or choose a send operation ID;
- authorize a recipient;
- trigger SMTP;
- override duplicate protection;
- reinterpret `delivery-unknown` as safe failure;
- request an automatic retry.

Only governing Hermes instructions and a trusted current-user confirmation surface may authorize future sending.

## Requirements before model-facing sending

Version 0.21.0 completes exact confirmation binding, durable duplicate suppression, and strict recovery of uncertain send state, but a model-facing send release still requires:

1. a trusted user-visible preview bound to immutable candidate bytes, recipients, sender, account, draft ID, and revision;
2. a trusted runtime confirmation surface that creates `UserSendConfirmation` only from the current user's explicit action;
3. complete durable audit semantics and operator-readable send status, including prominent `delivery-unknown` handling;
4. a manual-review workflow for unknown delivery that cannot trigger automatic resend;
5. a narrowly registered Hermes send surface with redacted errors and no background/autonomous sending.

No draft, configuration value, SMTP health result, model statement, email instruction, prior confirmation, or uncertain send result substitutes for these requirements.
