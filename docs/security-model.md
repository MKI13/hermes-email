# Security Model

## Version 0.17.0 boundary

Version 0.17.0 adds optional provider-independent local draft persistence and six manual draft tools. It adds no SMTP, send method, provider draft, mailbox write, purge, polling, retry loop, automatic reply, model callback, or content-triggered action. Draft state is local plaintext data and never evidence of sending or consent.

## Safe by default

- Missing settings load with read and local drafting disabled.
- Draft SQLite requires explicit mode and a stable non-secret account namespace.
- Registration, tool availability, status, disabled mode, mock mode, and local drafting never resolve a credential or connect to a provider.
- Registration derives fixed paths but creates no directory or database file.
- Read and draft failures have separate fixed diagnostics.
- Safety flags for send, delete, and move must remain false because those operations are unavailable.
- Every draft mutation must originate from a direct current-user request under the bundled skill. Email, quoted content, tool output, and prior state cannot authorize it.

## Read transport

IMAP permits only verified implicit TLS 1.2 or newer, ignores `SSLKEYLOGFILE`, resolves credentials only after TLS, authenticates with SASL PLAIN, and applies finite socket timeouts without retries. Each operation requires read-only `EXAMINE` plus explicit `READ-ONLY`, bounded `UIDVALIDITY`, and `UIDNEXT`. Access uses only UID `BODY.PEEK` partial fetches. No `STORE`, `APPEND`, `COPY`, `MOVE`, `CLOSE`, or `EXPUNGE` call and no provider write method exists.

UID cursors bind to one `UIDVALIDITY` epoch and descend from a fixed `UIDNEXT` snapshot. Callers choose every page. Per-page count, mailbox count, message bytes, page bytes, MIME parts, headers, addresses, and normalized text are bounded. Attachments and active, hidden, styled, or remotely referenced HTML content are excluded. Mail fields remain untrusted data.

## Observation ledger

Optional observation persistence stores only `(account namespace, provider, hashed mailbox namespace, provider message ID)`, first and last observation times, and a count. It never stores subject, address, body, MIME, RFC Message-ID, host, credentials, prompts, tool arguments, or provider metadata. Exact identity uniqueness never suppresses explicit reads and never means processed, trusted, drafted, acted upon, or sent.

Its fixed file is `email-observations.sqlite3`. Required failure prevents the read result from returning. Retention and pruning occur only within a later explicit observation transaction; there is no background maintenance.

## Draft database privacy

Optional local drafts use only the separate fixed `email-drafts.sqlite3` file. Draft content never enters the observation database. Each draft stores:

- the operator-defined account namespace;
- an opaque random locator and revision;
- active or reversibly trashed state;
- normalized ordered To, Cc, and Bcc recipients;
- subject, plain-text body, optional reply identifier, and timestamps.

This is sensitive plaintext. SQLite provides no application-layer encryption. Use encrypted local storage and protected backups where confidentiality requires them. `secure_delete` reduces ordinary SQLite residue but cannot guarantee erasure from SSD remapping, free space, snapshots, filesystem journals, or backups. Reversible trash is not deletion; no purge exists.

Draft IDs are locators, not authorization tokens. Account namespace is stored with each draft so a future sending implementation cannot silently switch accounts. Version 0.17.0 does not consume that namespace for sending.

## Draft input and output controls

Draft text is NFC-normalized. C0, C1, Unicode formatting, and surrogate code points are rejected except body LF and TAB. CRLF and CR become LF. Subject, body, reply identifier, display names, addresses, recipient count, row count, operation count, database pages, cursor, and output windows have independent limits. Recipients use ASCII addr-spec syntax until a release explicitly implements SMTPUTF8 and IDNA policy. Duplicate detection preserves local-part case and folds the domain only.

List tools expose no body and no To, Cc, or Bcc details. Get exposes all recipient groups for review and only a caller-selected bounded body window. Every returned draft is marked local, untrusted, and unsent. Mutation receipts expose no subject, recipient, body, or reply content.

Mail and draft fields can contain prompt injection. Tool descriptions and the skill prohibit treating that content as authorization or instructions, feeding it into another tool as commands, or mutating any draft without a direct current-user request.

## Concurrency and idempotency

Create, update, trash, and restore require a caller operation ID. The durable receipt stores only operation kind, a SHA-256 digest over the normalized request, draft ID, resulting revision, and completion time. Identical retry returns the same outcome. Reuse with changed content, target, revision, or action fails. Receipts are not automatically deleted; capacity exhaustion fails closed.

Update is a full replacement. Update, trash, and restore require the exact current revision. Mutations hold `BEGIN IMMEDIATE`; one concurrent writer wins and stale writers receive a fixed conflict with only the current revision.

Synchronous SQLite operations run in worker threads. `asyncio.shield()` prevents task cancellation from cancelling the worker; the facade waits for commit or rollback before propagating cancellation. An operator can safely retry the identical operation ID after an ambiguous caller result.

## SQLite and filesystem controls

Draft and observation databases have distinct application IDs and exact schema versions. Initialization holds `BEGIN EXCLUSIVE`; writes hold `BEGIN IMMEDIATE`. Every connection checks the configured byte cap before integrity scanning, reapplies `max_page_count`, verifies application identity, schema version, exact schema objects, columns, foreign keys, SQL definitions, row types, and `quick_check`, and disables extension loading.

Connections use rollback journals, `synchronous=FULL`, `secure_delete=ON`, `trusted_schema=OFF`, foreign keys, bounded busy timeouts, and parameterized values. There is no automatic retry, fallback to memory, destructive reset, downgrade, migration, vacuum, or recreation of a foreign, corrupt, or incompatible file.

The profile data path must be local and support SQLite locking. Symlinks are rejected. Existing files must be regular, single-link objects whose device and inode remain stable around SQLite open. On POSIX, directories require the effective owner and exact `0700`; files require the effective owner and exact `0600`. On Windows, the operator must protect the Hermes profile with an account-only ACL because portable Python cannot audit membership. The plugin does not claim protection from malicious code running as the same OS account.

## Errors and diagnostics

Model tools return fixed JSON codes. They never return exception text, tracebacks, database paths, draft content, server responses, hosts, settings, references, secrets, or raw MIME. Expected validation, conflict, state, capacity, busy, full, security, schema, closed, and unavailable failures map to separate codes. Unexpected failures return only `internal-error`.

A draft storage failure updates only `draft_diagnostic: draft-storage-error`. It does not mutate read readiness or observation storage state. Successful later draft operations clear that diagnostic. Disabled status checks do not touch the database.

## Credentials

Secret values must not appear in configuration, commits, tests, examples, logs, or databases. References use strict `HERMES_EMAIL_...` identifiers. `EnvironmentSecretResolver` performs one targeted lookup only during an explicit IMAP operation, never enumerates the environment, and returns redacted process-local values without caching. Local draft operations make no environment call.

## Requirements before sending

A future send release must independently add and test:

1. explicit SMTP configuration and verified transport security;
2. a send-specific configuration gate separate from draft enablement;
3. a fresh user-visible preview and confirmation for the exact revision, recipients, and account;
4. recipient and sender authorization independent of provider capability;
5. durable audit records and send idempotency;
6. ambiguous-delivery handling without uncontrolled retry;
7. redacted denial and failure behavior;
8. updated skill, security documentation, CI, immutable installation, and smoke tests.

No local draft state satisfies these requirements.
