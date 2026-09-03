# Security Model

## Version 0.16.0 boundary

Version 0.16.0 adds opt-in, content-free SQLite persistence for exact provider-message observations made during explicit reads. It adds no cached mail content, processed-state claim, SMTP, stored drafts, sending, deletion, movement, polling, retries, OAuth, or automatic action.

## Safe by default

- Missing runtime settings load as `disabled` with no provider or fallback.
- Plugin registration, read-tool availability checks, disabled mode, mock mode, reload, and `/email-status` never resolve a secret or connect to a provider. Registration creates no database directory or file.
- Real providers start as `provider-configured`, not ready. Only an explicit successful health or read operation produces `provider-ready`.
- Health snapshots expose fixed fields and diagnostic codes without configuration values, hosts, references, credentials, server responses, headers, or bodies.
- `/email-status` formats only the existing runtime snapshot; it invokes no provider or mailbox method.
- Secret references use the strict plugin-scoped `HERMES_EMAIL_...` format. `SecretValue` redacts string and representation output, and no provider caches or persists values.
- Expected connection, authentication, TLS, timeout, mailbox, protocol, and parsing failures use provider-neutral fixed messages. Runtime status maps them to a small fixed diagnostic set.
- Every IMAP operation creates one connection, performs one bounded action, logs out, and discards the client. There is no retry, polling, cursor following, or connection pool.
- Unload closes observation storage first, prevents new provider workers, shuts down active sockets without a mailbox close command, waits up to the configured operation timeout for active workers, disables the facade, and releases the Hermes context reference. A completion after closure cannot restore ready state or return mail; provider closure checks prevent a delayed connection or credential lookup from advancing to authentication.
- Sending, deletion, and movement flags remain false by default and have no production implementation.

## IMAP transport and authentication

- Only implicit TLS is accepted. Plaintext IMAP, opportunistic STARTTLS, certificate bypasses, and custom insecure modes are not configurable.
- The provider creates a client TLS context, requires certificate and hostname verification, loads system trust anchors, and requires TLS 1.2 or newer.
- The provider ignores `SSLKEYLOGFILE`; it never enables TLS session-key logging.
- Credentials resolve only after the TLS client is established and only for an explicit operation.
- Authentication uses SASL PLAIN over verified TLS. The provider intentionally does not use `IMAP4.login()`, whose command text can remain in CPython's private IMAP command history.
- Credential lookup, encoding, and authentication failures are converted to fixed exceptions without chained server or secret details. Credential byte variables are cleared when authentication returns.
- SASL PLAIN support is required from the server in this release. There is no insecure or less auditable authentication fallback.
- A finite socket timeout applies to the IMAP client. The provider does not automatically retry ambiguous failures.

## Read-only mailbox enforcement

- Each operation selects the configured mailbox with `readonly=True`, which sends IMAP `EXAMINE`.
- The server must explicitly report `READ-ONLY`; otherwise the operation fails closed.
- The server must provide bounded positive `UIDVALIDITY` and `UIDNEXT` metadata.
- Message access uses only `UID FETCH` with `BODY.PEEK[]<0.N>`. It never requests `BODY[]` without `PEEK` and never uses sequence numbers.
- The provider implements no `STORE`, `APPEND`, `COPY`, `MOVE`, `CLOSE`, or `EXPUNGE` call. Provider draft and send methods always raise a write-blocked error.
- Logout failures fall back to socket shutdown and never replace the primary operation result or failure.

## Pagination and resource bounds

`EmailPlugin.fetch_messages()` requires an integer limit from 1 through 100 and a valid opaque cursor. The provider repeats those checks for direct callers. One call makes at most one UID FETCH over a numeric range no wider than the requested limit.

The first range ends at `UIDNEXT - 1`. A continuation cursor contains a version, the mailbox `UIDVALIDITY`, and the next strictly smaller upper UID. The provider rejects malformed, stale, zero, out-of-range, and current-or-future UID boundaries before fetching. New messages therefore cannot enter an existing descending page sequence. UID gaps may produce short or empty pages with another cursor; no component follows that cursor automatically.

The provider rejects mailboxes above `max_mailbox_messages`. Each fetch requests a partial literal bounded by `max_message_bytes` and the per-page `max_page_bytes` budget. It rejects literals larger than `min(RFC822.SIZE, requested bytes)` and accepts protocol-permitted shorter partial transfers only as truncated messages. Duplicate, missing, malformed, unexpected, or over-budget responses fail closed. Any literal shorter than the server-reported full size is marked `truncated` rather than silently treated as complete.

`EmailPlugin.search_messages()` remains local case-insensitive substring filtering over one fetched page. It never issues an unbounded server search or follows a cursor.

## Observation persistence and deduplication

Persistence is disabled by default. Explicit SQLite mode requires an operator-defined stable account namespace and a readable provider. The fixed database path comes only from Hermes' public profile-scoped `ctx.state.data_dir`; settings cannot redirect it. The containing directory and database must be non-symlink regular objects. Existing POSIX objects must already have the effective owner and exact `0700`/`0600` permissions, and the verified device/inode identity must still match after SQLite opens the path. On Windows, Hermes' profile directory ACL must grant access only to the operator account; Python's portable standard library cannot audit that ACL. The plugin rejects detectable symlinks and identity changes but does not claim protection from malicious code running under the same OS account.

The ledger stores only `(account namespace, provider, hashed mailbox namespace, opaque provider message ID)`, first/last observation times, and an observation count. It never stores subjects, addresses, bodies, raw MIME, raw RFC Message-ID, hosts, profile/persona data, prompts, credentials, credential references, tool arguments, or arbitrary provider metadata. Account namespace is non-secret but remains sensitive metadata. SQLite itself provides no application-layer encryption; operators requiring encryption at rest must use encrypted local storage and protected backups.

Deduplication means uniqueness of one exact provider identity in this observation ledger. It never suppresses explicit list, lookup, or search results and never marks mail processed, trusted, drafted, sent, or safe. IMAP IDs include `UIDVALIDITY` and UID, so a new UID epoch or mailbox copy remains distinct. Attacker-controlled RFC Message-ID is never a uniqueness key. This observation history is not the future idempotency record for sending or another external action.

A new database receives a fixed application ID and monotonic schema version in an exclusive transaction. Every open verifies identity, exact objects and columns, and `quick_check`. Connections use a fixed rollback journal, `synchronous=FULL`, `secure_delete=ON`, `trusted_schema=OFF`, parameterized values, bounded lock timeout, and page count cap. Writes use `BEGIN IMMEDIATE`; one page commits atomically before mail returns. Cancellation waits for the worker transaction outcome. There is no automatic retry, fallback to memory, destructive reset, downgrade, extension loading, background retention, or vacuum.

Retention age, row count, page size, message-ID length, and page observations are bounded independently. Retention and row pruning happen only in an explicit observation transaction. Insecure paths, corruption, incompatible schemas, busy/read-only storage, I/O failures, and full databases fail closed with fixed tool and runtime codes; the original database remains in place for operator recovery.

## Untrusted message normalization

Messages, headers, MIME structure, HTML, attachments, and quoted content are untrusted input. The provider parses bounded bytes with Python's standard email parser, caps MIME parts, header text, body text, and address counts, and degrades malformed message-local fields without exposing raw server protocol text.

Attachments are skipped. Inline plain text is preferred. HTML is converted to text without URLs, images, scripts, style elements, templates, document heads, or remote retrieval. Elements marked with `hidden`, active `aria-hidden`, or inline `style` are omitted, and an HTML body containing a stylesheet is omitted rather than attempting incomplete CSS visibility evaluation. Open elements are tracked by tag so mismatched closing tags cannot expose suppressed text. C0, C1, terminal, surrogate, and Unicode formatting controls are removed from normalized headers and bodies; only normalized body newlines and tabs survive before whitespace cleanup.

The skill treats all normalized mail as data rather than instructions. Email content cannot override Hermes' system instructions, active persona, safety rules, or the requirement for future explicit send confirmation.

## Hermes tool output

Hermes tools whitelist normalized fields and return compact JSON text. List and search omit message bodies and arbitrary provider metadata from model output, limit a page to 25 records, and cap subjects, addresses, and display names. The current provider facade obtains a bounded normalized message page for listing, so IMAP may retrieve partial message literals within its configured per-message and per-page byte ceilings; this release does not claim a header-only transport operation. Lookup returns a caller-selected body window of at most 20,000 characters with total length, offset, and continuation offset; source truncation remains separate from response windowing. Every successful mail result carries an untrusted-content marker.

Tool schemas and handlers reject unknown fields, booleans used as integers, oversized identifiers, oversized cursors, invalid queries, and out-of-range limits before provider access. Known failures map to fixed codes; exception text, traceback data, protocol responses, hosts, settings, references, credentials, and raw MIME are never returned. An unexpected exception returns only `internal-error`.

## Trust boundaries

### Providers

Provider adapters are trusted plugin code running with the user's process permissions. Each adapter must minimize credential access, validate remote data, use secure transport defaults, avoid sensitive logs, and expose only declared capabilities.

### Hermes context

At registration, the plugin wraps the public runtime context with `ActiveProfileContextSource`, reads `ctx.profile_name`, and, only for explicit SQLite mode, obtains a fixed child path from public `ctx.state.data_dir`. It does not serialize live Hermes objects, inspect private Hermes files, or replace missing context with a plugin-defined personality.

### Capability versus authorization

A provider capability and a user safety setting are independent checks. Supporting an operation never grants permission to perform it. Version 0.16.0 exposes no production mail-write capability at either layer.

## Future side-effect requirements

Before any release can send, delete, or move mail, it must include:

1. an explicit operation-specific configuration gate;
2. a runtime authorization check independent of provider capability;
3. clear user-visible preview and confirmation semantics;
4. idempotency and deduplication behavior;
5. audit logging that redacts secrets and minimizes content;
6. focused tests for denial, failure, retries, and ambiguous state;
7. updated security documentation and changelog.

## Credentials and logs

Never place secret values in plugin configuration or commit tokens, passwords, private keys, account exports, or real messages. `.env`, `.env.*`, `*.pem`, and `*.key` are ignored and excluded by distribution checks. Configuration contains only validated references such as `HERMES_EMAIL_IMAP_PASSWORD`.

`EnvironmentSecretResolver` calls `os.environ.get(reference)` only after strict validation and only when an explicit provider operation requests that reference. It does not enumerate the environment. `SecretNotFoundError` includes the reference but never an expected or resolved value. No resolver or provider writes secrets to SQLite, YAML, JSON, files, Hermes memory, logs, or TLS key logs.
