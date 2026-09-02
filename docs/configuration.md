# Configuration

## Principles

Configuration is profile- and deployment-owned. The repository contains no personal addresses, provider credentials, company rules, or fixed writing style.

`hermes.profile: auto` means that future integrations should use the active Hermes profile. Version 0.1.0 resolves only the public Hermes plugin property `ctx.profile_name`; it does not inspect private profile files.

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

- `provider`: provider identifier or `null`. No identifiers are implemented yet.
- `read_mode`: `disabled` or `mock`.
- `draft_mode`: `disabled` or `mock`.

### `hermes`

- `profile`: `auto` or a future explicit profile identifier. Version 0.1.0 stores and validates this value but does not switch profiles.

### `behavior`

All inheritance flags default to `true`. They express the intended behavior of future context adapters; they do not authorize private runtime access.

### `safety`

`allow_send`, `allow_delete`, and `allow_move` all default to `false`. Version 0.1.0 does not implement these operations even if a local test configuration changes a flag to `true`.

## Loading

```python
from hermes_email import load_config

config = load_config("path/to/config.yaml")
```

The loader uses YAML safe loading, validates section and value types, and rejects unknown keys to surface mistakes early.

## Secrets

Do not put secrets in this configuration file or commit them to Git. Future providers must use Hermes-supported secret handling or another documented secure credential source. Provider credentials must remain separate from non-secret behavior and safety settings.
