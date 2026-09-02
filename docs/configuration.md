# Configuration

## Principles

Configuration is profile- and deployment-owned. The repository contains no personal addresses, provider credentials, company rules, or fixed writing style.

The `profile: auto` setting in the `hermes` section means that integrations should use the active Hermes profile. During plugin registration, version 0.12.0 binds only the public Hermes plugin property `ctx.profile_name`; it does not inspect private profile files.

## Hermes runtime settings

Hermes owns the configuration location. Place non-secret settings under `plugins.entries.hermes-email.settings` in the normal Hermes configuration surface. The plugin reads only its own `email`, `hermes`, `behavior`, and `safety` sections through `ctx.get_config()`:

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
        safety:
          allow_send: false
          allow_delete: false
          allow_move: false
```

If these settings are absent, the plugin loads as `disabled` without selecting a provider. Valid explicit mock settings produce `mock-ready`. Invalid settings or unsupported providers produce `configuration-error` with a fixed non-sensitive diagnostic code while skill and command registration continue.

Use `/email-status` in a Hermes session to display only the existing runtime health snapshot. The command neither displays the configuration nor invokes a provider or mailbox operation.

## Standalone configuration example

```yaml
email:
  provider: null
  read_mode: disabled
  draft_mode: mock

hermes:
  profile: auto

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

- `provider`: explicit provider identifier or `null`. Version 0.12.0 accepts only `mock`; `null` and empty values do not select a fallback.
- `read_mode`: `disabled` or `mock`. `EmailPlugin.fetch_messages()`, `EmailPlugin.get_message()`, and `EmailPlugin.search_messages()` are blocked unless this is explicitly `mock`.
- `draft_mode`: `disabled` or `mock`.

### `hermes`

- `profile`: `auto` or a future explicit profile identifier. Version 0.12.0 stores and validates this value but does not switch profiles.

### `behavior`

All inheritance flags default to `true`. They express the intended behavior of future context adapters; they do not authorize private runtime access.

### `safety`

`allow_send`, `allow_delete`, and `allow_move` all default to `false`. Version 0.12.0 does not implement these operations even if a local test configuration changes a flag to `true`.

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

The factory delegates provider creation exclusively to the resolver and preserves the supplied configuration, including all safety settings. The resolver trims and case-normalizes the configured identifier. It creates `MockEmailProvider` only for an explicit `mock` value and rejects every other value without importing modules or executing configuration content.

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

All retrieval facades reject disabled reading and missing providers before calling the provider. `fetch_messages()` and `search_messages()` require fetch capability, accept only integer limits from 1 through 100, and accept only `None` or a non-empty cursor string. They forward a valid cursor byte-for-byte as opaque provider data and request exactly one provider page; callers must explicitly request any next page. Local search first validates and trims a non-empty query of at most 256 characters, then performs only plain substring matching over subject, sender address, sender display name, and body text while preserving provider order. Its returned `EmailMessagePage.messages` contains only matches from the current provider page. Its `next_cursor` is the unchanged provider-page cursor and indicates only that the provider has another message page, not that another search match exists.

## Secrets

Do not put secrets in this configuration file or commit them to Git. Future providers must use Hermes-supported secret handling or another documented secure credential source. Provider credentials must remain separate from non-secret behavior and safety settings.
