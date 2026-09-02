# Hermes Email

[![CI](https://github.com/MKI13/hermes-email/actions/workflows/ci.yml/badge.svg)](https://github.com/MKI13/hermes-email/actions/workflows/ci.yml)

Hermes Email is a universal, provider-neutral email plugin and email skill for [Hermes Agent](https://github.com/NousResearch/hermes-agent).

## Philosophy

Hermes remains the intelligence, personality, and decision-maker. The plugin provides technical infrastructure; the skill provides email-specific operating guidance. Neither defines a fixed personality, company voice, language, or user-specific behavior.

A friendly German Hermes profile should produce friendly, concise German drafts. A formal English profile should preserve that profile's language and style. Provider adapters must not alter this behavior.

## Version 0.13.0

This release adds a provider-neutral foundation for resolving future credentials without adding a production mail provider:

- configuration stores only optional `username_ref` and `password_ref` identifiers;
- references must use the plugin-scoped `HERMES_EMAIL_...` format;
- `EnvironmentSecretResolver` reads exactly one validated reference on explicit request;
- `SecretValue` redacts both string and representation output and is never serialized or persisted;
- plugin registration, disabled mode, and mock mode never request a secret;
- the mock-only provider boundary, bounded caller-driven pagination, disabled sending, and all existing safety controls remain unchanged.

### Runtime health

| State | Meaning |
|---|---|
| `disabled` | No explicit provider is configured; no mail operation is available. |
| `mock-ready` | The explicit mock provider resolved successfully. |
| `configuration-error` | Expected settings validation or provider resolution failed; Hermes registration continues. |

Type `/email-status` in a Hermes session to display the fixed fields from `EmailPlugin.get_runtime_status()`: `version`, `state`, `provider`, `profile`, `read_enabled`, `draft_enabled`, `send_enabled`, and `diagnostic`.

### Deliberately not included

Version `0.13.0` does not connect to production mail accounts, fetch real messages, send email, delete or move messages, run background polling, implement OAuth, classify mail, automate replies, route LLM calls, or persist state in a database.

## Safety defaults

| Operation | Version 0.13.0 |
|---|---|
| Read mail | Disabled or mock only |
| Prepare a draft | Local value/mock only |
| Send mail | Unconditionally unavailable |
| Delete mail | Unavailable |
| Move mail | Unavailable |
| Connect an account | Unavailable |

No credentials are required. `.env` files and common private-key formats are ignored by Git.

## Credential references

Secret values must never be placed in plugin settings. Future providers may receive validated references instead:

```yaml
credentials:
  username_ref: HERMES_EMAIL_USERNAME
  password_ref: HERMES_EMAIL_PASSWORD
```

The referenced environment values are available only through an explicit `SecretResolver.get_secret()` call. Version 0.13.0 makes no such call during registration, disabled operation, or mock operation. The resolver does not enumerate the environment, expand shell syntax, read files, use the network, log values, or cache them.

## Installation and skill loading

Hermes supports standalone directory plugins with a root `plugin.yaml` and `__init__.py`. After this repository is published, Hermes users can install and enable it with the standard plugin commands:

```bash
hermes plugins install MKI13/hermes-email
hermes plugins enable hermes-email
hermes plugins doctor hermes-email --ci
```

The plugin registers the read-only skill as `hermes-email:email`, the in-session command `/email-status`, and one official unload callback for runtime context cleanup. Version 0.13.0 registers no tools, model hooks, account integrations, or background tasks.

## Development

Hermes Email targets Python 3.11 through 3.13, matching Hermes Agent's current supported range.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pytest
python -m build
python scripts/check_dist.py
```

CI installs Hermes Agent from an immutable upstream v0.21.0 commit using its supported editable development mode, then runs `hermes plugins doctor . --ci` with an empty home directory. Hermes v0.21.0 has no separate non-interactive security-scan command; the security scan remains an official pre-release installation gate.

The example configuration is at [`examples/config.example.yaml`](examples/config.example.yaml).

## Documentation

- [Architecture](docs/architecture.md)
- [Configuration](docs/configuration.md)
- [Security model](docs/security-model.md)
- [Hermes compatibility](docs/official-hermes-compatibility.md)
- [Contributing](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md)

## License

[MIT](LICENSE)
