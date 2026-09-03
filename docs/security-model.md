# Security Model

## Version 0.18.0 boundary

Version 0.18.0 adds a production SMTP transport primitive and pure technical candidate gates. Both are disconnected internal library APIs: `EmailPlugin` does not own them, and no Hermes tool, command, hook, callback, timer, poller, retry worker, or automatic action can reach SMTP DATA. The release adds no confirmed send method, provider draft, mailbox write, purge, polling, or automatic reply. Draft, SMTP configuration, recipient policy, and armed technical gates are never evidence of current-user confirmation or sending.

## Safe by default

- Missing settings load with read and local drafting disabled.
- Draft SQLite requires explicit mode and a stable non-secret account namespace.
- Registration, tool availability, status, disabled mode, mock mode, and local drafting never resolve a credential or connect to a provider.
- Registration derives fixed paths but creates no directory or database file.
- Read and draft failures have separate fixed diagnostics.
- Delete and move flags must remain false. `allow_send` defaults false and can arm only pure technical candidate gates when SMTP, drafts, account identity, fixed sender, credentials, and recipient policy are complete; runtime sending remains false.
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

Draft IDs are locators, not authorization tokens. Account namespace is stored with each draft so a future sending implementation cannot silently switch accounts. Candidate preparation requires that namespace to exactly match SMTP configuration, but no runtime send path consumes the candidate.

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

## SMTP security and outcome certainty

SMTP defaults to disabled and recipient policy defaults to deny. Submission settings require a stable namespace matching enabled local drafts, an ASCII host and fixed sender, separate strict credential references, verified implicit TLS or mandatory STARTTLS, bounded timeouts, and a final serialized-message byte cap. Plaintext, opportunistic downgrade, certificate bypass, hostname bypass, SMTPUTF8, IDNA, custom headers, HTML, and attachments are unavailable.

TLS uses system trust, hostname verification, TLS 1.2 or newer, no `SSLKEYLOGFILE` processing, and no protocol debug transcript. STARTTLS performs EHLO before and after the mandatory upgrade. Secrets resolve only after TLS state and AUTH PLAIN capability are verified. Non-ASCII or NUL credentials fail with fixed errors because the selected standard-library mechanism requires ASCII.

Technical preparation reads one exact active draft revision inside a SQLite snapshot and binds it to the configured account, fixed sender, caller-supplied validated Message-ID and UTC-normalized date, every To/Cc/Bcc envelope recipient, and deterministic MIME bytes. Recipient authorization is exact and deployment-owned. Bcc never appears in headers. The provider-local draft reply locator is not assumed to be an RFC Message-ID and is not emitted as `In-Reply-To`.

The transport revalidates framing, addresses, duplicate recipients, Bcc absence, line lengths, fixed sender, local size cap, and advertised server SIZE before submission. It sends MAIL once, requires every RCPT before DATA, calls RSET on any RCPT rejection, and invokes DATA at most once. Pre-DATA timeout, connection, protocol, TLS, authentication, sender, recipient, and size failures are definite non-send results. A final non-250 DATA reply is definite rejection. Any exception or interruption after DATA begins is delivery-unknown and receives no automatic retry. Final 250 means only accepted by the configured server; later QUIT failure cannot change that result.

Close prevents new operations, closes active clients, and waits only for the configured timeout. Production socket operations are timeout-bounded. The same-account malicious-code exclusion also means the transport is not a security boundary against another local process importing and invoking it directly.

## Errors and diagnostics

Model tools return fixed JSON codes. They never return exception text, tracebacks, database paths, draft content, server responses, hosts, settings, references, secrets, or raw MIME. Expected validation, conflict, state, capacity, busy, full, security, schema, closed, and unavailable failures map to separate codes. Unexpected failures return only `internal-error`.

A draft storage failure updates only `draft_diagnostic: draft-storage-error`. It does not mutate read readiness or observation storage state. Successful later draft operations clear that diagnostic. Disabled status checks do not touch the database.

## Credentials

Secret values must not appear in configuration, commits, tests, examples, logs, or databases. References use strict `HERMES_EMAIL_...` identifiers. `EnvironmentSecretResolver` performs one targeted lookup only during an explicit IMAP operation or after an SMTP caller has established verified TLS and checked AUTH PLAIN. It never enumerates the environment and returns redacted process-local values without caching. Registration, status, local draft operations, and candidate preparation make no environment call.

## Requirements before sending

A model-facing send release must still independently add and test:

1. a fresh user-visible preview bound to immutable candidate bytes, exact draft revision, recipients, sender, and account;
2. explicit current-user confirmation for that exact preview;
3. durable audit and send-intent records written before transport dispatch;
4. caller operation IDs and idempotent duplicate suppression across restart and ambiguous caller results;
5. terminal accepted, rejected, definite non-send, and delivery-unknown states;
6. a durable prohibition on retrying delivery-unknown outcomes;
7. a narrowly registered Hermes send surface whose denials and failures remain redacted.

No draft, configuration value, technical candidate, or SMTP health result satisfies these requirements. Version 0.19.0 owns this bridge.
