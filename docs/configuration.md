# Configuration

## Principles

Configuration is profile- and deployment-owned. The repository contains no personal addresses, provider credentials, company rules, or fixed writing style. `hermes.profile: auto` means integrations use the active Hermes profile. Version 0.17.0 binds only public plugin properties and never inspects private profile files.

## Hermes runtime settings

Place settings and secret references under `plugins.entries.hermes-email.settings`. The plugin reads only its `email`, `hermes`, `credentials`, `imap`, `storage`, `drafts`, `behavior`, and `safety` sections through `ctx.get_config()`:

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
        safety:
          allow_send: false
          allow_delete: false
          allow_move: false
```

Absent settings load with read and draft access disabled. Valid mock settings produce `mock-ready`; valid IMAP settings produce `provider-configured` without connecting. Local drafts are independent of these read states and require no provider. Invalid self-contained settings fail closed; unsupported provider resolution produces `configuration-error` without secret or network access.

`/email-status` displays only the existing runtime snapshot. Nine tools are registered statically, while offline availability checks expose read and draft tools only when their independent gates permit them.

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

### `behavior`

All inheritance flags default to `true`. They express intended behavior for context adapters and do not authorize private runtime access.

### `safety`

`allow_send`, `allow_delete`, and `allow_move` must remain `false`. Version 0.17.0 rejects `true` because those operations are unavailable. Local draft permission comes only from explicit `drafts.mode: sqlite`; it does not authorize sending or mailbox changes.

## Standalone example and loading

The complete default configuration is in `examples/config.example.yaml`.

```python
from hermes_email import load_config

config = load_config("path/to/config.yaml")
```

The loader uses YAML safe loading, validates every section and value type, and rejects unknown keys. Pass the validated configuration to the provider resolver only when constructing a read provider. Local draft storage is created by the plugin runtime from public profile state.
