# Configuration

## Principles

Operators define settings per Hermes profile and deployment. Project sources include no personal addresses, provider credentials, company rules, or fixed writing style. `hermes.profile: auto` means integrations use the active Hermes profile. Version 0.21.0 binds only public plugin properties and never inspects private profile files.

## Hermes runtime settings

Place settings and secret references under `plugins.entries.hermes-email.settings`. The plugin reads only its `email`, `hermes`, `credentials`, `imap`, `storage`, `drafts`, `smtp`, `recipient_policy`, `behavior`, and `safety` sections through `ctx.get_config()`.

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

Absent settings load with read, draft, SMTP, and technical-send access disabled. Valid mock settings produce `mock-ready`; valid IMAP settings produce `provider-configured` without connecting. Local drafts are independent of these read states and require no provider. Invalid self-contained settings fail closed.

`/email-status` displays only the fixed runtime snapshot. Nine tools are registered statically. No SMTP or send tool or command is registered in version 0.21.0.

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
        drafts:
          mode: sqlite
          account_namespace: primary-account
```

Observation and draft namespaces serve different identities and may differ. The observation namespace identifies one provider mailbox. The draft namespace binds drafts to one operator-intended account independently of provider access.

## Core fields

### `email`

- `provider`: `null`, `mock`, or `imap`.
- `read_mode`: `disabled`, `mock`, or `readonly`.

### `hermes`

- `profile`: `auto` or a future explicit profile identifier. This release does not switch profiles.

### `imap`

- `host`: required ASCII DNS name or IP address.
- `port`: default `993`.
- `security`: exactly `tls`.
- `username_ref` and `password_ref`: strict `HERMES_EMAIL_...` references, resolved only during explicit IMAP operations.
- `mailbox`: default `INBOX`; all access is read-only.
- finite bounds apply to timeout, mailbox size, message bytes, and page bytes.

### `storage`

- `mode`: `disabled` or `sqlite`.
- `account_namespace`: stable non-secret mailbox identity.
- retention, row count, and database size are bounded.

The fixed file is `email-observations.sqlite3` under profile-scoped plugin data and contains identity/timing metadata only.

### `drafts`

- `mode`: `disabled` or `sqlite`.
- `account_namespace`: stable intended sending-account identity.
- `max_drafts`, `max_operations`, and `max_database_bytes` are bounded.

The fixed file is `email-drafts.sqlite3`. It contains sensitive plaintext draft content and must live on protected storage.

### `smtp`

- `mode`: `disabled` or `submission`.
- `account_namespace`: must exactly match the enabled draft namespace.
- `host`, `port`, and `security`: verified implicit TLS or mandatory STARTTLS only.
- `username_ref` and `password_ref`: separate SMTP credential references.
- `sender_address`: fixed configured ASCII sender.
- `sender_display_name`: optional bounded display name.
- finite timeout and serialized-message byte limits apply.

SMTP configuration alone creates no connection, resolves no secret, and exposes no Hermes dispatch surface.

### `recipient_policy`

- `mode`: `deny`, `allowlist`, or `all`.
- `allowed_addresses`: exact ASCII addresses.
- `allowed_domains`: exact lowercase ASCII domains; subdomains are not implicit.

All To/Cc/Bcc recipients must pass the policy before candidate preparation. Policy is deployment authorization, not current-user confirmation.

### `behavior`

All inheritance flags default to `true`. They describe intended Hermes-context inheritance and do not authorize private runtime access.

### `safety`

`allow_delete` and `allow_move` must remain `false`. `allow_send: true` only arms technical candidate eligibility when SMTP, drafts, account identity, fixed sender, credentials, and recipient policy are complete.

Version 0.21.0 additionally requires:

1. a trusted `UserSendConfirmation` for the exact draft ID and exact revision before candidate preparation;
2. a unique opaque `send_operation_id` before transport dispatch;
3. a durable send intent committed before SMTP is called;
4. no redispatch when that intent already exists;
5. rejection of a second operation ID for the same `(draft_id, revision)`;
6. recovery of prior-process or legacy unresolved `dispatching` as terminal `delivery-unknown`;
7. manual external verification before any human chooses a separate corrective action after unknown delivery.

The internal send-intent file is fixed as `email-send-intents.sqlite3` under the caller-provided profile data directory. Schema v2 stores only operation/draft/revision/confirmation identity, request digest, fixed state, internal dispatcher ownership, and timestamps. It stores no mail body, subject, recipient addresses, credentials, or raw MIME.

The registered Hermes runtime still does not instantiate the send orchestrator or SMTP transport for model use. `send_enabled` remains false and no model-facing send tool exists.

## Idempotency and uncertainty semantics

A `send_operation_id` must be 16 to 128 ASCII characters with no whitespace. Reusing the same ID with the same exact candidate returns the stored state without SMTP. Reusing it with changed candidate content fails closed.

A unique database constraint on `(draft_id, revision)` blocks a second send intent for the same reviewed draft revision even when a new operation ID or new confirmation token is supplied.

Persisted states are:

- `dispatching` — live current-process attempt only;
- `accepted` — SMTP server returned final acceptance;
- `definite-failure` — failure known not to be an uncertain post-DATA result;
- `delivery-unknown` — acceptance cannot be safely determined.

A current-process `dispatching` record is replayed without a second SMTP call. If its dispatcher belongs to an earlier process or is absent after v0.20 migration, it is atomically converted to `delivery-unknown`.

`delivery-unknown` is not equivalent to failure. The message may already have been accepted. Automatic retry is forbidden and callers must surface manual-review semantics.

## Standalone example and loading

The complete default configuration is in `examples/config.example.yaml`.

```python
from hermes_email import load_config

config = load_config("path/to/config.yaml")
```

The loader uses YAML safe loading, validates every section and value type, and rejects unknown keys.
