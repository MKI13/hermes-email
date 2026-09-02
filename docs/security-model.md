# Security Model

## Version 0.13.0 boundary

This release is a foundation with a local mock provider, not a mail client. The mock performs no network access, account authentication, mailbox polling, message transmission, deletion, movement, or persistence.

## Safe by default

- Secret references contain identifiers only; plugin settings never contain credential values.
- References are restricted to the plugin-scoped `HERMES_EMAIL_...` format before any environment lookup.
- Plugin registration, disabled mode, mock mode, and reload never resolve a secret.
- Secret values have redacted string and representation output and are neither serialized nor persisted.
- Missing runtime settings load successfully as `disabled` with no provider and no fallback.
- Runtime settings are read only through Hermes' plugin-scoped `ctx.get_config()` API.
- Expected validation and provider-resolution failures become `configuration-error`; unrelated programming failures are not swallowed.
- Health snapshots expose fixed fields and diagnostic codes without settings values, credentials, or message content.
- `/email-status` formats only `get_runtime_status()` from the registered runtime instance.
- The status command ignores arguments and invokes no mailbox method, provider operation, tool, file access, environment access, or network client.
- Reading is `disabled` by default and has no production implementation.
- `EmailPlugin.fetch_messages()` requires explicit mock read mode, a configured provider, declared fetch capability, an integer limit from 1 through 100, and either `None` or a non-empty opaque cursor string.
- The plugin forwards valid cursors unchanged, never decodes or creates them, and performs exactly one provider fetch per call. It does not clamp limits, follow `next_cursor`, retry, or persist cursor state.
- `EmailPlugin.get_message()` uses the shared read gates, requires declared get capability, accepts only a non-empty string identifier, and delegates it unchanged as opaque data.
- Fetch and lookup return provider results without mailbox mutation; search returns a new locally filtered page. All retrieval facades propagate provider failures unchanged.
- `EmailPlugin.search_messages()` first validates a query of at most 256 characters, then requires the read and fetch gates and the same strict limit and opaque-cursor gates as fetch. It makes exactly one provider fetch and performs only local case-insensitive substring matching over that page.
- Search returns only current-page matches and forwards the provider page's `next_cursor` unchanged. That cursor signals another provider message page, not guaranteed additional search matches, and is never followed automatically.
- Mock reading returns only deterministic synthetic messages. The mock provider alone creates and interprets deterministic local pagination cursors; unknown cursors fail explicitly.
- Mock drafts exist only in the provider instance's memory.
- `EmailPlugin.send_message()` always raises `SendingUnavailableError`.
- `MockEmailProvider.send_message()` always raises `MockSendBlockedError`.
- Base provider capabilities default to false; the mock enables only fetch, get, and drafts.
- `EmailPlugin.from_config()` delegates provider selection only to the fixed resolver.
- Provider resolution requires an explicit value and recognizes only `mock`.
- Unknown or suspicious provider strings are rejected without dynamic imports or fallback selection.
- Sending, deletion, and movement configuration flags default to false.
- No credential values are required or included; optional configuration fields hold references only.

A future provider capability and a user safety setting are separate checks. Supporting an operation must never imply permission to perform it.

## Trust boundaries

### Email content

Messages, headers, attachments, and quoted text are untrusted input. They may contain prompt injection, deceptive instructions, malicious links, or sensitive data. The skill instructs Hermes to treat them as content rather than agent authority.

### Providers

Future provider adapters run as trusted plugin code with the user's process permissions. Each adapter must minimize credential access, validate remote data at the provider boundary, use secure transport defaults, and avoid logging message bodies or secrets.

### Hermes context

At registration, the plugin wraps the public runtime context with `ActiveProfileContextSource` and reads only `ctx.profile_name`. The runtime reference is owned by an official `ctx.on_unload()` callback and released during unload. Snapshot fields without a stable public API remain empty. The plugin does not serialize live Hermes objects, scrape private internal files, or replace missing context with a plugin-defined personality.

## Future side-effect requirements

Before any release can send, delete, or move mail, it must include:

1. an explicit operation-specific configuration gate;
2. a runtime authorization check independent of provider capability;
3. clear user-visible preview and confirmation semantics;
4. idempotency and deduplication behavior;
5. audit logging that redacts secrets and minimizes content;
6. focused tests for denial, failure, retries, and ambiguous state;
7. updated security documentation and changelog.

No item in this list is implemented implicitly by the version 0.13.0 interfaces.

## Credentials and logs

Never place secret values in plugin configuration or commit tokens, passwords, private keys, account exports, or real messages. `.env`, `.env.*`, `*.pem`, and `*.key` are ignored and excluded by distribution checks. Configuration may contain only validated references such as `HERMES_EMAIL_PASSWORD`.

`EnvironmentSecretResolver` calls `os.environ.get(reference)` only after strict validation and only when a future provider explicitly requests that reference. It does not enumerate the environment. `SecretNotFoundError` includes the reference but never an expected or resolved value. `SecretValue` reveals its value only through an explicit provider-facing method; string formatting and `repr()` remain redacted. No resolver writes SQLite, YAML, JSON, files, Hermes memory, or logs.
