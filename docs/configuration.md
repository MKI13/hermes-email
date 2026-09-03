# Configuration

## Principles

Configuration is profile- and deployment-owned. The repository contains no personal addresses, provider credentials, company rules, or fixed writing style.

The `profile: auto` setting in the `hermes` section means that integrations should use the active Hermes profile. During plugin registration, version 0.14.0 binds only the public Hermes plugin property `ctx.profile_name`; it does not inspect private profile files.

## Hermes runtime settings

Hermes owns the configuration location. Place non-secret settings and secret references under `plugins.entries.hermes-email.settings` in the normal Hermes configuration surface. The plugin reads only its own `email`, `hermes`, `credentials`, `imap`, `behavior`, and `safety` sections through `ctx.get_config()`:

```yaml
plugins:
  entries:
    hermes-email:
      settings:
        email:
          provider: mock
          read_mode: mock
          draft_mode: mock
        hermes:
          profile: auto
        credentials:
          username_ref: null
          password_ref: null
        safety:
          allow_send: false
          allow_delete: false
          allow_move: false
```

If these settings are absent, the plugin loads as `disabled` without selecting a provider. Valid mock settings produce `mock-ready`; valid IMAP settings produce `provider-configured` without connecting. An explicit successful health or read operation produces `provider-ready`. Expected failures produce fixed redacted status states, while invalid settings or unsupported providers produce `configuration-error` and registration continues.

Use `/email-status` in a Hermes session to display only the existing runtime health snapshot. The command neither displays the configuration nor invokes a provider or mailbox operation.

A read-only IMAP setup uses provider-specific references and disables drafts:

```yaml
plugins:
  entries:
    hermes-email:
      settings:
        email:
          provider: imap
          read_mode: readonly
          draft_mode: disabled
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
```

## Standalone configuration example

```yaml
email:
  provider: null
  read_mode: disabled
  draft_mode: mock

hermes:
  profile: auto

credentials:
  username_ref: null
  password_ref: null

imap:
  host: null
  port: 993
  security: tls
  username_ref: null
  password_ref: null
  mailbox: INBOX
  timeout_seconds: 15
  max_mailbox_messages: 10000
  max_message_bytes: 2000000
  max_page_bytes: 5000000

behavior:
  inherit_persona: true
  inherit_language: true
  inherit_style: true
  inherit_user_preferences: true
  inherit_safety_rules: true

safety:
  allow_send: false
  allow_delete: false
  allow_move: false
```

The complete example is in `examples/config.example.yaml`.

## Fields

### `email`

- `provider`: explicit provider identifier or `null`. Version 0.14.0 accepts `mock` and `imap`; `null` and empty values do not select a fallback.
- `read_mode`: `disabled`, `mock`, or `readonly`. Mock requires `mock`; IMAP accepts `readonly` or `disabled`.
- `draft_mode`: `disabled` or `mock`.

### `hermes`

- `profile`: `auto` or a future explicit profile identifier. Version 0.14.0 stores and validates this value but does not switch profiles.

### `credentials`

- `username_ref`: reserved optional provider-neutral username reference.
- `password_ref`: reserved optional provider-neutral password reference.

Both fields contain references, never credential values. These provider-neutral placeholders remain accepted for compatibility with version 0.13.0; version 0.14.0 does not resolve them. IMAP uses its own references so later read and send transports can use different accounts safely.

### `imap`

- `host`: required ASCII DNS name or IP address when `provider: imap`; URLs, user information, paths, whitespace, and control characters are rejected.
- `port`: implicit-TLS endpoint, default `993`, bounded to `1..65535`.
- `security`: exactly `tls`; plaintext, opportunistic STARTTLS, and certificate bypasses are not configurable.
- `username_ref` and `password_ref`: required together for IMAP and resolved only during an explicit health or read operation.
- `mailbox`: printable ASCII mailbox name, default `INBOX`; every operation opens it read-only with `EXAMINE`.
- `timeout_seconds`: finite per-socket-operation timeout from 1 through 120 seconds.
- `max_mailbox_messages`: maximum accepted mailbox count, default 10000 and maximum 50000.
- `max_message_bytes`: maximum partial content request for one lookup, default 2000000 and maximum 10000000.
- `max_page_bytes`: total per-page partial-content budget, default 5000000 and maximum 20000000.

All references are at most 128 characters, start with `HERMES_EMAIL_`, and contain only uppercase letters, digits, and single underscores between segments. Values such as `HOME`, paths, shell expressions, templates, and dotted identifiers are rejected before environment access. Registration, disabled mode, mock mode, and `/email-status` never resolve secrets.

### `behavior`

All inheritance flags default to `true`. They express the intended behavior of future context adapters; they do not authorize private runtime access.

### `safety`

`allow_send`, `allow_delete`, and `allow_move` all default to `false`. Version 0.14.0 does not implement these operations even if a local test configuration changes a flag to `true`.

## Loading

```python
from hermes_email import load_config

config = load_config("path/to/config.yaml")
```

The loader uses YAML safe loading, validates section and value types, and rejects unknown keys to surface mistakes early. Pass the resulting configuration to the explicit provider resolver:

```python
from hermes_email import load_config
from hermes_email.plugin import EmailPlugin

config = load_config("path/to/config.yaml")
plugin = EmailPlugin.from_config(config)
```

The factory delegates provider creation exclusively to the fixed resolver and preserves all safety settings. It creates `MockEmailProvider` only for explicit `mock` and a disconnected `ImapReadOnlyProvider` only for complete `imap` settings. It performs no dynamic imports, secret lookup, DNS lookup, socket creation, authentication, or provider fallback.

Mock retrieval must be explicitly enabled and bounded:

```python
config = EmailPluginConfig.from_mapping({
    "email": {"provider": "mock", "read_mode": "mock"},
})
plugin = EmailPlugin.from_config(config)
first_page = await plugin.fetch_messages(limit=2)
if first_page.next_cursor is not None:
    second_page = await plugin.fetch_messages(
        limit=2,
        cursor=first_page.next_cursor,
    )
message = await plugin.get_message("mock-message-customer-001")
search_page = await plugin.search_messages("sample service", limit=2)
if search_page.next_cursor is not None:
    next_search_page = await plugin.search_messages(
        "sample service",
        limit=2,
        cursor=search_page.next_cursor,
    )
```

For IMAP, call `await plugin.check_provider_health()` only when a live TLS/authentication check is intended, then call `fetch_messages()` or `get_message()` explicitly. IMAP pages scan one descending UID window, so UID gaps may yield a short or empty page with `next_cursor`; callers may request that cursor but the plugin never does so automatically. A message whose server-reported size exceeds the configured partial limit has `metadata["truncated"] == "true"`.

All retrieval facades reject disabled reading and missing providers before calling the provider. `fetch_messages()` and `search_messages()` require fetch capability, accept only integer limits from 1 through 100, and accept only `None` or a non-empty cursor string. They forward a valid cursor byte-for-byte as opaque provider data and request exactly one provider page; callers must explicitly request any next page. Local search first validates and trims a non-empty query of at most 256 characters, then performs only plain substring matching over subject, sender address, sender display name, and body text while preserving provider order. Its returned `EmailMessagePage.messages` contains only matches from the current provider page. Its `next_cursor` is the unchanged provider-page cursor and indicates only that the provider has another message page, not that another search match exists.

## Secrets

Do not put secrets in this configuration file or commit them to Git. Set the environment names referenced by `imap.username_ref` and `imap.password_ref` in the user-managed Hermes process environment. The built-in resolver reads only those exact validated names during explicit IMAP operations and does not cache them. Provider credentials remain separate from non-secret behavior and safety settings.
