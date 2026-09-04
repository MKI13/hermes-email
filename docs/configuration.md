# Configuration

## Principles

Operators define settings per Hermes profile and deployment. Project sources include no personal addresses, provider credentials, company rules, or fixed writing style. `hermes.profile: auto` means integrations use the active Hermes profile. Version 0.19.0 binds only public plugin properties and never inspects private profile files.

## Hermes runtime settings

Place settings and secret references under `plugins.entries.hermes-email.settings`. The plugin reads only its `email`, `hermes`, `credentials`, `imap`, `storage`, `drafts`, `smtp`, `recipient_policy`, `behavior`, and `safety` sections through `ctx.get_config()`:

```yaml
plugins:
  entries:
    hermes-email:
      settings:
        email:
          provider: mock
          read_mode: mock
        hermes:
          profile: auto
        credentials:
          username_ref: null
          password_ref: null
        storage:
          mode: disabled
          account_namespace: null
        drafts:
          mode: sqlite
          account_namespace: primary-account
          max_drafts: 1000
          max_operations: 10000
          max_database_bytes: 33554432
        smtp:
          mode: disabled
        recipient_policy:
          mode: deny
        safety:
          allow_send: false
          allow_delete: false
          allow_move: false
```

Absent settings load with read, draft, SMTP, and technical-send access disabled. Valid mock settings produce `mock-ready`; valid IMAP settings produce `provider-configured` without connecting. Local drafts are independent of these read states and require no provider. Invalid self-contained settings fail closed; unsupported provider resolution produces `configuration-error` without secret or network access.

`/email-status` displays only the fixed runtime snapshot, including non-secret `SMTP` configuration and `Technical send gates` state. Nine tools are registered statically, while offline availability checks expose only read and draft tools when their independent gates permit them. No SMTP or send tool or command is registered.

## Read-only IMAP with independent storage

```yaml
plugins:
  entries:
    hermes-email:
      settings:
        email:
          provider: imap
          read_mode: readonly
        imap:
          host: mail.example.com
          port: 993
          security: tls
          username_ref: HERMES_EMAIL_IMAP_USERNAME
          password_ref: HERMES_EMAIL_IMAP_PASSWORD
          mailbox: INBOX
          timeout_seconds: 15
          max_mailbox_messages: 10000
          max_message_bytes: 2000000
          max_page_bytes: 5000000
        storage:
          mode: sqlite
          account_namespace: primary-inbox
          retention_days: 90
          max_observations: 10000
          max_database_bytes: 16777216
        drafts:
          mode: sqlite
          account_namespace: primary-account
```

Observation and draft namespaces serve different identities and may differ. The observation namespace identifies one provider mailbox. The draft namespace binds drafts to one operator-intended account independently of provider access.

## Fields

### `email`

- `provider`: `null`, `mock`, or `imap`; no fallback provider is selected.
- `read_mode`: `disabled`, `mock`, or `readonly`. Mock requires `mock`; IMAP accepts `readonly` or `disabled`.

Provider draft modes do not exist. Local drafts are configured only in `drafts`.

### `hermes`

- `profile`: `auto` or a future explicit profile identifier. This release stores and validates the value but does not switch profiles.

### `credentials`

- `username_ref`: reserved optional provider-neutral username reference.
- `password_ref`: reserved optional provider-neutral password reference.

Both fields contain references, never credential values. This release does not resolve the provider-neutral placeholders. IMAP uses its own references so future transports can use separately authorized accounts.

### `imap`

- `host`: required ASCII DNS name or IP address for IMAP; URLs, user information, paths, whitespace, and controls are rejected.
- `port`: implicit-TLS endpoint, default `993`, bounded to `1..65535`.
- `security`: exactly `tls`; plaintext, STARTTLS, and certificate bypasses are unavailable.
- `username_ref` and `password_ref`: required together and resolved only during an explicit health or read operation.
- `mailbox`: printable ASCII name, default `INBOX`; every operation uses read-only `EXAMINE`.
- `timeout_seconds`: finite socket-operation timeout from 1 through 120 seconds.
- `max_mailbox_messages`: default 10000, maximum 50000.
- `max_message_bytes`: default 2000000, maximum 10000000.
- `max_page_bytes`: default 5000000, maximum 20000000.

References are at most 128 characters, start with `HERMES_EMAIL_`, and contain uppercase letters, digits, and single underscores between segments. Registration, local drafting, disabled mode, mock mode, and status never resolve them.

### `storage`

- `mode`: `disabled` or explicit `sqlite`.
- `account_namespace`: required with SQLite; 1 to 64 ASCII letters, digits, dots, underscores, or hyphens, beginning with a letter or digit. Use a different stable value for each account and mailbox; do not use an address, hostname, credential, or personal label.
- `retention_days`: default 90, bounded from 1 through 3650.
- `max_observations`: default 10000, maximum 100000.
- `max_database_bytes`: default 16777216, bounded from 1048576 through 1073741824.

Observation SQLite requires a readable provider. Its path is fixed to `email-observations.sqlite3` under public profile-scoped plugin data. The first explicit successful read opens it. The ledger contains identity and timing metadata, never content.

### `drafts`

- `mode`: `disabled` by default or explicit `sqlite`.
- `account_namespace`: required with SQLite and validated with the same portable syntax as observation storage. It identifies the intended future sending account, not a provider mailbox. Keep it stable and distinct across accounts.
- `max_drafts`: default 1000, bounded from 1 through 10000.
- `max_operations`: default 10000, bounded from 1 through 100000. Completed mutation receipts are not automatically pruned; reaching the cap fails closed.
- `max_database_bytes`: default 33554432, bounded from 1048576 through 268435456.

Draft SQLite is provider-independent. Its path is fixed to `email-drafts.sqlite3` under public profile-scoped plugin data and cannot be redirected. Registration computes the path without creating a directory or file; the first explicit draft operation opens it. No draft content is stored in `email-observations.sqlite3`.

Use a local filesystem with SQLite locking semantics; network filesystems are unsupported. POSIX paths require exact owner-only permissions. On Windows, configure the profile ACL for account-only access. Draft SQLite is plaintext and contains sensitive content; use encrypted storage and protected backups where required.

### `smtp`

- `mode`: `disabled` by default or explicit `submission`.
- `account_namespace`: required for submission and must exactly equal the enabled draft namespace.
- `host`: required ASCII DNS name or IP address; URLs, paths, whitespace, and controls are rejected.
- `port`: default `465`, bounded to `1..65535`; choose the operator's verified implicit-TLS or STARTTLS submission endpoint.
- `security`: `implicit_tls` or mandatory `starttls`; plaintext and opportunistic downgrade are unavailable.
- `username_ref` and `password_ref`: separate SMTP references required together for submission and resolved only after verified TLS and AUTH PLAIN capability checks.
- `sender_address`: required fixed ASCII envelope/header sender. A draft or model cannot replace it.
- `sender_display_name`: optional bounded Unicode display name; CR, LF, NUL, and other controls are rejected.
- `timeout_seconds`: finite operation timeout from 1 through 120 seconds, default 15.
- `max_message_bytes`: final serialized-message cap from 1024 through 10000000 bytes, default 1000000.

SMTP configuration requires enabled local draft storage even when `safety.allow_send` remains false. Configuration alone creates no connection, resolves no secret, and exposes no dispatch surface.

### `recipient_policy`

- `mode`: `deny` by default, `allowlist`, or explicit `all`.
- `allowed_addresses`: at most 100 exact ASCII addr-spec values. Domain comparison is case-insensitive; local-part comparison remains case-sensitive.
- `allowed_domains`: at most 100 exact lowercase ASCII domains. A domain entry does not include subdomains.

Lists are allowed only with `allowlist`, which requires at least one entry. To, Cc, and Bcc recipients must all pass the policy before a candidate is prepared. Policy is deployment authorization, not current-user confirmation.

### `behavior`

All inheritance flags default to `true`. They express intended behavior for context adapters and do not authorize private runtime access.

### `safety`

`allow_delete` and `allow_move` must remain `false`. `allow_send: true` is valid only with complete SMTP submission settings, matching enabled draft storage, and a non-deny recipient policy. In v0.19.0 this flag arms only technical candidate eligibility. It does not indicate user confirmation, set runtime `send_enabled`, instantiate a transport, or authorize a Hermes send.

Version 0.19.0 additionally requires an internal trusted `UserSendConfirmation` before `prepare_send_candidate()` can succeed. That confirmation must match the exact `draft_id` and exact `revision` requested for preparation. A missing confirmation, another draft ID, or another revision fails closed. Any draft update changes the revision and invalidates previous confirmation automatically. Current model output, email or draft content, configuration, recipient policy, SMTP state, and `allow_send` are never valid substitutes for the current user's explicit approval.

The bundled Hermes skill and registered tools still cannot create this trusted confirmation or dispatch SMTP. Local draft permission does not authorize sending or mailbox changes. Durable audit and idempotent orchestration remain prerequisites before a model-facing send surface is allowed.

## Standalone example and loading

The complete default configuration is in `examples/config.example.yaml`.

```python
from hermes_email import load_config

config = load_config("path/to/config.yaml")
```

The loader uses YAML safe loading, validates every section and value type, and rejects unknown keys. Pass the validated configuration to the provider resolver only when constructing a read provider. Local draft storage is created by the plugin runtime from public profile state.
