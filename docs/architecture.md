# Architecture

## Purpose

Hermes Email separates technical email infrastructure from agent behavior. The active Hermes agent remains responsible for language, persona, reasoning, user preferences, and decisions.

## Components

### Hermes directory-plugin entry point

The root `plugin.yaml` targets Hermes manifest v1 and `__init__.py` follows the native standalone directory-plugin entry-point convention. Manifest schema selection is independent of the runtime context API. `register(ctx)` reads plugin-scoped settings through `ctx.get_config()`, creates one `EmailPlugin` runtime, binds `ActiveProfileContextSource`, registers `/email-status` through `ctx.register_command()`, three read tools through `ctx.register_tool()`, one `ctx.on_unload()` cleanup callback, and the bundled skill. Registration creates no network client or database file, invokes no provider operation, and starts no model hook, poller, or background task. Enabled persistence takes its fixed database path only from the public `ctx.state.data_dir` property.

### Plugin facade

`hermes_email.plugin.EmailPlugin` is the provider-neutral orchestration point. It owns validated configuration, an optional provider, an optional observation store, an optional Hermes context source, and a non-sensitive runtime state. `EmailPlugin.from_config(config)` remains the exclusive provider factory. Real providers begin as `provider-configured`; an explicit health or successful read advances them to `provider-ready`, while expected failures become fixed authentication or reachability states. `get_runtime_status()` never invokes a mailbox operation.

The retrieval facades share the `read_mode` and provider-presence gates. Fetch additionally requires fetch capability, accepts only limits from 1 through 100, validates an optional non-empty opaque cursor without transforming it, and delegates exactly one page request. Search first validates and trims a query of at most 256 characters, then applies the same read, fetch-capability, limit, and cursor gates. It delegates exactly one provider page request and performs case-insensitive plain substring matching over subject, sender address, sender display name, and body text. The returned `EmailMessagePage` preserves provider order, contains only matches from that page, and carries the provider page's unchanged `next_cursor`.

Future technical responsibilities belong behind this facade:

- provider connection lifecycle;
- message ingestion and normalized events;
- draft storage;
- durable processing state distinct from observation history;
- privacy-aware logging;
- independent safety authorization.

Version 0.16.0 optionally records exact provider-message observations after explicit mock or IMAP retrieval. Pagination remains caller-driven and repeated observations never suppress explicit list, lookup, or search results. Durable processing state, message-content caching, and every production mail write path remain unimplemented.

### Observation storage

`hermes_email.storage.SqliteObservationStore` is an opt-in content-free ledger behind `EmailPlugin`. Each successful explicit provider result is checked against its requested page limit, then persisted in a short `asyncio.to_thread` operation before mail returns to Hermes. Cancellation waits for the SQLite transaction outcome; provider network work finishes before any database transaction starts. A required-store failure prevents the read result from returning and changes runtime state to the fixed `storage-error` state.

One observation identity contains an explicit operator-owned account namespace, provider name, SHA-256 mailbox namespace, and the opaque provider message ID. IMAP message IDs include `UIDVALIDITY` and UID. RFC Message-ID, subject, sender, recipients, body, host, credential references, raw MIME, and arbitrary metadata are not stored. Observation count does not mean processed, trusted, drafted, or sent.

SQLite uses a fixed application ID and monotonic schema version. Creation and verification check a profile-owned directory, regular single-link file, stable device/inode identity, owner-only POSIX permissions, expected schema objects, `quick_check`, a fixed rollback-journal mode, full synchronous writes, secure deletion, a bounded busy timeout, and `max_page_count`. Writes use `BEGIN IMMEDIATE`, uniqueness enforces deduplication, and retention plus row caps run only in the same explicit transaction. Corrupt, incompatible, insecure, busy, read-only, or full storage is never deleted, recreated, downgraded, retried, or replaced with memory.

### Provider abstraction

`hermes_email.providers.EmailProvider` defines `fetch_messages(*, limit=50, cursor=None) -> EmailMessagePage` plus asynchronous health, lookup, draft, send, and lifecycle methods. `MockEmailProvider` provides deterministic local pages and in-memory mock drafts. `ImapReadOnlyProvider` provides only health, fetch, and lookup; its capabilities explicitly deny drafts and sends.

The IMAP provider creates a new connection for each explicit operation, resolves credentials only after a verified TLS connection exists, authenticates with SASL PLAIN, opens the configured mailbox read-only, and requires `READ-ONLY`, `UIDVALIDITY`, and `UIDNEXT` responses. Fetch pages cover one bounded UID range and use partial `BODY.PEEK` literals. Cursors bind the next decreasing UID boundary to `UIDVALIDITY`; sparse windows may return short or empty pages, and callers decide whether to request another page. MIME normalization excludes attachments and remote resources, converts HTML to plain text, strips control characters, and reports truncation. Provider shutdown prevents new workers, closes active sockets without mailbox mutation, and waits up to the configured operation timeout for active workers before unload returns; closed-state checks prevent delayed workers from authenticating or returning mail.

Future read or send adapters must normalize provider data into `EmailMessage` and `EmailDraft`. A provider's declared capability is never sufficient authorization for an external or destructive action.

### Secret resolution

`SecretResolver` is the provider-neutral credential boundary. `CredentialReferences` stores only optional `HERMES_EMAIL_...` identifiers. `EnvironmentSecretResolver` validates one identifier before calling the injected environment getter exactly once and returns a process-local `SecretValue` with redacted string and representation output. Resolution performs no enumeration, expansion, file access, network access, persistence, or caching.

Hermes Agent v0.21.0 provides an API for plugins that implement secret-source backends, but its plugin context does not provide a public secret-read method. Hermes documents environment loading as the standard credential path, so this release uses targeted environment lookup. The fixed resolver may construct an environment resolver for IMAP, but neither construction nor registration reads a value. Only an explicit IMAP operation calls it.

### Provider resolver

`resolve_email_provider(config)` normalizes the explicitly configured provider name and compares it with a fixed allowlist containing `mock` and `imap`. IMAP resolution constructs a disconnected provider from validated settings and a resolver; it performs no secret lookup, DNS lookup, socket creation, authentication, dynamic import, discovery, or fallback selection.

### Hermes context adapter

`HermesContext` holds optional values for profile name, persona, system prompt, language, writing style, user preferences, skills, tools, safety rules, and custom instructions.

Hermes exposes `ctx.profile_name` and the profile-scoped `ctx.state.data_dir` as public plugin APIs. `ActiveProfileContextSource` reads only that property. `register(ctx)` binds this source to the runtime `EmailPlugin`; `get_hermes_context()` returns an owned snapshot with the active profile name. Other values remain empty until Hermes provides an appropriate public API or an explicit caller supplies them. The project does not read private Hermes files or invent a fallback personality.

### Hermes read tools

`hermes_email.tools` registers `email_list_messages`, `email_get_message`, and `email_search_messages` in the `hermes_email` toolset with Hermes' public asynchronous tool API. A rejected name rolls back every earlier registration in the toolset and fails plugin loading rather than accepting a foreign or partial toolset. A side-effect-free availability check exposes them only while the runtime has explicit mock or read-only provider access. Handlers call only the guarded retrieval facade and never dispatch another tool.

Handlers return compact JSON strings. List and search expose bounded metadata without bodies; lookup exposes caller-selected body windows capped at 20,000 characters. Results carry explicit untrusted-content, source-truncation, and body-window fields. Invalid input, disabled access, unsupported capability, stale identifiers, provider failures, and unexpected failures become fixed codes without exception text or mail content.

### Status command

`/email-status` is an in-session slash command registered with Hermes' public `ctx.register_command()` API. Its handler calls `get_runtime_status()` on the registered runtime and formats only that immutable snapshot. It does not access configuration mappings, provider message methods, tools, environment variables, files, or network resources.

### Email skill

`skill/SKILL.md` describes mail-specific behavior while explicitly inheriting the active Hermes context. It treats email content as untrusted, distinguishes drafting from sending, and claims no mailbox side effects.

## Dependency direction

```text
Hermes runtime
    -> register(ctx)
        -> ctx.get_config(plugin-relative sections)
        -> EmailPluginConfig validation
        -> explicit provider resolver when configured
        -> disabled | mock-ready | configuration-error
        -> ActiveProfileContextSource(ctx)
        -> EmailPlugin
        -> ctx.register_command("email-status", same runtime)
            -> get_runtime_status()
            -> fixed text formatter
        -> email skill
        -> ctx.on_unload(runtime release)

EmailPlugin.from_config
    -> EmailPluginConfig
    -> Provider resolver
        -> MockEmailProvider (only for explicit mock)
    -> EmailPlugin

EmailPlugin
    -> HermesContextSource (optional)
    -> EmailProvider (optional; MockEmailProvider for local tests)

EmailPlugin.fetch_messages(limit, cursor)
    -> shared read-only gates
    -> provider fetch-capability gate
    -> integer limit gate: 1..100, without clamping
    -> cursor gate: None or non-empty string, without normalization
    -> EmailProvider.fetch_messages(limit=limit, cursor=cursor unchanged)
    -> exactly one EmailMessagePage; no automatic continuation

EmailPlugin.get_message(message_id)
    -> shared read-only gates
    -> provider get-capability gate
    -> non-empty string ID gate
    -> EmailProvider.get_message(message_id unchanged)

EmailPlugin.search_messages(query, limit, cursor)
    -> non-empty query of at most 256 characters
    -> shared read-only gates
    -> provider fetch-capability gate
    -> shared integer limit gate: 1..100, without clamping
    -> shared cursor gate: None or non-empty string, without normalization
    -> exactly one EmailProvider.fetch_messages(limit=limit, cursor=cursor unchanged)
    -> local plain substring filtering of that page only
    -> EmailMessagePage(matches, provider page next_cursor unchanged)

EmailProvider
    -> provider-neutral models
```

The skill does not depend on a provider. Providers do not define persona or behavioral rules.

## Extension rules

1. Add one provider or capability per focused change.
2. Keep provider-specific data behind the provider interface.
3. Preserve safe defaults and add independent authorization checks before side effects.
4. Use stable public Hermes APIs; do not couple to private files or process globals.
5. Keep profile-specific behavior in Hermes context or user configuration.
6. Add tests and documentation with every new operational capability.
