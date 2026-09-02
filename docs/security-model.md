# Security Model

## Version 0.10.1 boundary

This release is a foundation with a local mock provider, not a mail client. The mock performs no network access, account authentication, mailbox polling, message transmission, deletion, movement, or persistence.

## Safe by default

- Manifest v1 compatibility changes only installer metadata; runtime configuration and authorization behavior are unchanged.
- Missing runtime settings load successfully as `disabled` with no provider and no fallback.
- Runtime settings are read only through Hermes' plugin-scoped `ctx.get_config()` API.
- Expected validation and provider-resolution failures become `configuration-error`; unrelated programming failures are not swallowed.
- Health snapshots expose fixed fields and diagnostic codes without settings values, credentials, or message content.
- `/email-status` formats only `get_runtime_status()` from the registered runtime instance.
- The status command ignores arguments and invokes no mailbox method, provider operation, tool, file access, environment access, or network client.
- Reading is `disabled` by default and has no production implementation.
- `EmailPlugin.fetch_messages()` requires explicit mock read mode, a configured provider, declared fetch capability, and a finite positive integer limit.
- `EmailPlugin.get_message()` uses the shared read gates, requires declared get capability, accepts only a non-empty string identifier, and delegates it unchanged as opaque data.
- Retrieval facades return provider results without mailbox mutation and propagate provider failures unchanged.
- `EmailPlugin.search_messages()` requires the read and fetch gates, validates a query of at most 256 characters, fetches at most 100 messages, and performs only local case-insensitive substring matching.
- Mock reading returns only deterministic synthetic messages.
- Mock drafts exist only in the provider instance's memory.
- `EmailPlugin.send_message()` always raises `SendingUnavailableError`.
- `MockEmailProvider.send_message()` always raises `MockSendBlockedError`.
- Base provider capabilities default to false; the mock enables only fetch, get, and drafts.
- `EmailPlugin.from_config()` delegates provider selection only to the fixed resolver.
- Provider resolution requires an explicit value and recognizes only `mock`.
- Unknown or suspicious provider strings are rejected without dynamic imports or fallback selection.
- Sending, deletion, and movement configuration flags default to false.
- No credentials are required or included.

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

No item in this list is implemented implicitly by the version 0.10.1 interfaces.

## Credentials and logs

Never commit tokens, passwords, private keys, account exports, or real messages. `.env`, `.env.*`, `*.pem`, and `*.key` are ignored. Future logs should record bounded operational metadata and opaque identifiers, not message bodies or credentials.
