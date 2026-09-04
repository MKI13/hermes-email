# Architecture

## Purpose

Hermes Email separates technical mail infrastructure from agent behavior. Hermes remains responsible for reasoning, persona, language, style, user preferences, and decisions.

## Runtime assembly

The root `plugin.yaml` uses Hermes manifest v1. Root `__init__.py` exports `register(ctx)`, which reads only plugin-scoped settings through `ctx.get_config()`, binds public `ctx.profile_name`, derives fixed persistence paths from public `ctx.state.data_dir` only when a store is enabled, creates one `EmailPlugin`, registers `/email-status`, nine tools, one unload callback, and the bundled skill.

Registration creates no network client or database file, resolves no secret, invokes no provider or store operation, and starts no model hook, callback, timer, poller, retry loop, or background task. Tool registration is all-or-nothing: a rejected draft registration disposes prior draft tools; a rejected read registration also disposes all draft tools.

## Plugin facade

`hermes_email.plugin.EmailPlugin` owns validated configuration, an optional read provider, an optional content-free observation store, an optional local draft store, an optional Hermes context source, and separate redacted read and draft diagnostics. It does not own or instantiate an SMTP transport. `EmailPlugin.from_config()` is the provider factory. Draft operations never call it or require a provider.

Read methods enforce explicit read mode and provider capabilities before one bounded provider call. Search filters one fetched page locally. Provider order and opaque cursors are preserved. Explicit reads may atomically record observations before results return.

Draft methods enforce explicit local-store availability and execute one synchronous SQLite operation through `asyncio.to_thread()`. `asyncio.shield()` and post-cancellation waiting ensure that cancellation is propagated only after the transaction has committed or rolled back. A retry uses the same operation ID to retrieve the durable outcome. Draft failures update only the independent fixed draft diagnostic and cannot poison read readiness or observation state.

Unload marks the facade closed, detaches provider and stores, clears context, closes both stores, and then closes the provider. No late draft operation can restore enabled status.

## Provider abstraction

`EmailProvider` exposes health, one-page fetch, single-message lookup, and lifecycle methods. Its capability record contains only `fetch` and `get`. No provider draft, send, delete, or move method exists.

`MockEmailProvider` supplies deterministic local read fixtures. `ImapReadOnlyProvider` creates a connection for each explicit operation, establishes verified implicit TLS 1.2 or newer, resolves credentials after TLS, authenticates with SASL PLAIN, opens the mailbox with read-only `EXAMINE`, validates `READ-ONLY`, `UIDVALIDITY`, and `UIDNEXT`, and accesses messages only through bounded UID `BODY.PEEK` partial fetches. Callers alone decide whether to follow an opaque descending cursor. MIME normalization excludes attachments and active or remote HTML content.

## SMTP transport and technical gates

`SmtplibTransport` is a separate internal seam rather than an `EmailProvider` capability. It supports verified implicit TLS and mandatory STARTTLS using system trust, hostname verification, TLS 1.2 or newer, no key logging, a fixed local EHLO identity, disabled protocol debug, and SASL PLAIN after TLS. Separate SMTP secrets resolve lazily only after TLS and capability checks. Credentials must be ASCII because the standard-library AUTH API cannot safely represent other values in this mode.

`SmtpSubmission` owns one validated ASCII envelope and exact CRLF-framed message byte sequence. It rejects Bcc headers, NUL, bare line endings, overlong lines, duplicate recipients, invalid addresses, and byte-limit violations. The transport rechecks the configured fixed sender and final configured byte limit before opening a connection.

A submission uses one connection and no retry. MAIL must succeed, then every RCPT must succeed before DATA. Any RCPT rejection triggers RSET and no DATA. A non-250 final DATA reply is a definite rejection. A transport exception, timeout, or interruption after DATA begins becomes `SmtpDeliveryUnknownError`; a final 250 becomes accepted by the server and remains accepted if QUIT fails.

`prepare_send_candidate()` is a non-network technical gate. In version 0.19.0 it requires a trusted `UserSendConfirmation` bound to the exact draft ID and exact revision in addition to explicit deployment enablement, matching SMTP/draft namespaces, one exact active draft revision, at least one bounded recipient, authorization of every To/Cc/Bcc address, the fixed sender, a caller-supplied validated Message-ID and aware date, and the final serialized-byte cap. Missing confirmation or a confirmation for another draft or revision fails closed before draft access. Any draft mutation increments the revision and therefore invalidates a prior confirmation automatically.

The confirmation object is a trusted-runtime proof only. Model output, email content, draft content, configuration, recipient policy, SMTP readiness, or `safety.allow_send` cannot create or substitute current-user confirmation. Candidate preparation emits deterministic plain-text MIME with quoted-printable body encoding, no Bcc header, no HTML, no attachments, no custom headers, and no `In-Reply-To` derived from the provider-local draft locator.

Version 0.19.0 still leaves SMTP dispatch disconnected from `EmailPlugin`, Hermes tools, commands, callbacks, hooks, and timers. Deployment `allow_send` arms only technical eligibility. Runtime `send_enabled` remains false. Durable send audit, immutable send-intent persistence, idempotent dispatch, and delivery-unknown recovery remain prerequisites before a Hermes send surface can be exposed.

## Observation storage

`SqliteObservationStore` is an optional content-free ledger. One identity contains an operator account namespace, provider, SHA-256 mailbox namespace, and provider message ID. IMAP IDs include `UIDVALIDITY` and UID. RFC Message-ID, subject, addresses, body, host, credentials, raw MIME, and arbitrary metadata are never stored. Observation never means processed, trusted, drafted, acted upon, or sent.

The fixed `email-observations.sqlite3` file opens lazily after an explicit read. Uniqueness deduplicates exact provider identities while repeated reads remain visible. Required-store failures prevent mail return and set the read runtime to `storage-error`.

## Local draft storage

`SqliteDraftStore` is an independent optional store at fixed `email-drafts.sqlite3`. It contains sensitive plaintext draft content and never shares a table or transaction with observation storage. Every draft stores the explicit account namespace, opaque random ID, monotonically increasing revision, active or trashed state, normalized To/Cc/Bcc rows, subject, plain-text body, optional reply identifier, and timestamps.

Inputs use NFC normalization. Subjects are limited to 500 characters and 2,000 UTF-8 bytes; bodies to 20,000 characters and 80,000 bytes; reply identifiers to 512 characters and 2,048 bytes; and all recipient groups together to 50 addresses. Addresses use ASCII addr-spec syntax in this release. Domain comparison is case-insensitive for duplicate detection; local-part case is preserved. Control and formatting code points are rejected except body LF and TAB, and body CRLF/CR is normalized to LF.

Create, update, trash, and restore require a 16-to-128-character operation ID. A SHA-256 digest binds the operation kind and complete normalized request. A completed receipt stores only operation kind, digest, draft ID, resulting revision, and completion time. Identical reuse returns the prior outcome; changed reuse fails. Receipts are bounded and never automatically pruned.

Update fully replaces one exact active revision. Trash and restore reversibly change one exact revision. One concurrent writer succeeds; a stale caller receives the current revision without content. There is no purge or hard-delete operation.

## SQLite and path controls

Each database has a distinct fixed application ID and schema version. Initialization uses `BEGIN EXCLUSIVE`; mutations use `BEGIN IMMEDIATE`. Every connection verifies file size before integrity work, application identity, schema version, exact tables, indexes, columns, foreign keys, schema SQL, `quick_check`, and page count limits. Connections use rollback journals, `synchronous=FULL`, `secure_delete=ON`, `trusted_schema=OFF`, foreign keys, bounded busy timeouts, disabled extension loading, and parameterized values.

The parent path is non-symlink and owner-controlled. Existing files must be regular, single-link objects with stable device/inode identity around SQLite open. POSIX requires `0700` directories and `0600` files. Foreign, corrupt, incompatible, insecure, busy, read-only, or full databases remain in place; there is no reset, recreation, migration, automatic retry, or memory fallback. Windows confidentiality depends on an operator-managed account-only profile ACL. Same-account malicious code is outside the threat model.

## Hermes tools

Three read tools provide bounded list, lookup, and local search. Six draft tools create, list, get, update, trash, and restore local records. All return compact JSON with whitelisted fields and fixed errors. Handlers never return exception text, configuration, filesystem paths, provider responses, credentials, or arbitrary metadata and never dispatch another tool.

Draft lists omit bodies and all recipient details, including Bcc. Draft get returns explicit To/Cc/Bcc plus a caller-selected body window of at most 20,000 characters and marks the content as untrusted and unsent. Mutation receipts are content-free. Tool descriptions and the skill require a direct current-user request for every mutation; content inside mail or a draft has no authority.

## Status and context

`/email-status` formats only `EmailPlugin.get_runtime_status()`. It invokes no provider, store, file, environment, secret, or tool operation. Read readiness and draft-store readiness are independent. SMTP configuration and armed technical-gate booleans are non-secret configuration facts; they do not make sending available.

`ActiveProfileContextSource` reads only public `ctx.profile_name`. Other persona and preference fields remain empty unless a public API or explicit caller supplies them. The plugin never reads private Hermes files or invents a fallback personality.

## Dependency direction

```text
Hermes register(ctx)
    -> validated plugin settings
    -> optional disconnected read provider
    -> optional fixed observation path
    -> optional fixed draft path
    -> EmailPlugin
        -> three read tools -> read facade -> provider -> optional observation ledger
        -> six draft tools -> local draft facade -> separate draft database
        -> /email-status -> immutable redacted snapshot
        -> unload -> both stores and provider close
    -> email skill

Disconnected internal library only:
    trusted current-user confirmation + exact active draft revision
        -> confirmation + technical gates -> immutable SMTP bytes
    immutable SMTP bytes -> single-attempt SMTP transport
    (no arrow from EmailPlugin, tool, command, hook, callback, or timer)
```

The skill is provider-independent. Providers never define persona or behavior. A stored draft grants no external authorization, and a confirmation grants authorization only for its exact draft revision.

## Extension rules

1. Keep provider-specific data behind read providers.
2. Keep draft and observation privacy classes in separate databases.
3. Add independent configuration, runtime authorization, review, confirmation, audit, and idempotency gates before any external side effect.
4. Use stable public Hermes APIs only.
5. Keep profile-specific behavior in Hermes context or user configuration.
6. Add bounded tests and current security documentation with every capability.
