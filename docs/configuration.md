# Configuration

## Principles

Configuration is profile- and deployment-owned. The repository contains no personal addresses, provider credentials, company rules, or fixed writing style.

`hermes.profile: auto` means that integrations should use the active Hermes profile. During plugin registration, version 0.7.0 binds only the public Hermes plugin property `ctx.profile_name`; it does not inspect private profile files.

## Example

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

- `provider`: explicit provider identifier or `null`. Version 0.7.0 accepts only `mock`; `null` and empty values do not select a fallback.
- `read_mode`: `disabled` or `mock`. `EmailPlugin.fetch_messages()` and `EmailPlugin.get_message()` are blocked unless this is explicitly `mock`.
- `draft_mode`: `disabled` or `mock`.

### `hermes`

- `profile`: `auto` or a future explicit profile identifier. Version 0.7.0 stores and validates this value but does not switch profiles.

### `behavior`

All inheritance flags default to `true`. They express the intended behavior of future context adapters; they do not authorize private runtime access.

### `safety`

`allow_send`, `allow_delete`, and `allow_move` all default to `false`. Version 0.7.0 does not implement these operations even if a local test configuration changes a flag to `true`.

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
messages = await plugin.fetch_messages(limit=10)
message = await plugin.get_message("mock-message-customer-001")
```

Both facades reject disabled reading and missing providers before calling the provider. List retrieval requires fetch capability and rejects non-positive or non-integer limits. Single-message retrieval requires get capability, accepts only a non-empty string, and forwards the identifier unchanged as opaque data.

## Secrets

Do not put secrets in this configuration file or commit them to Git. Future providers must use Hermes-supported secret handling or another documented secure credential source. Provider credentials must remain separate from non-secret behavior and safety settings.
