# Hermes Email

Hermes Email is a universal, provider-neutral email plugin and email skill for [Hermes Agent](https://github.com/NousResearch/hermes-agent).

## Philosophy

Hermes remains the intelligence, personality, and decision-maker. The plugin provides technical infrastructure; the skill provides email-specific operating guidance. Neither defines a fixed personality, company voice, language, or user-specific behavior.

A friendly German Hermes profile should produce friendly, concise German drafts. A formal English profile should preserve that profile's language and style. Provider adapters must not alter this behavior.

## Version 0.1.0

This release establishes a safe, extensible foundation:

- an official Hermes directory-plugin manifest and `register(ctx)` entry point;
- a namespaced email skill;
- typed, provider-neutral message and draft models;
- an asynchronous `EmailProvider` abstraction;
- a `HermesContext` model and minimal public profile adapter;
- validated multi-user configuration with safe defaults;
- documentation and foundation tests.

### Deliberately not included

Version `0.1.0` does not connect to production mail accounts, fetch real messages, send email, delete or move messages, run background polling, implement OAuth, classify mail, automate replies, route LLM calls, or persist state in a database.

## Safety defaults

| Operation | Version 0.1.0 |
|---|---|
| Read mail | Disabled or mock only |
| Prepare a draft | Local value/mock only |
| Send mail | Unconditionally unavailable |
| Delete mail | Unavailable |
| Move mail | Unavailable |
| Connect an account | Unavailable |

No credentials are required. `.env` files and common private-key formats are ignored by Git.

## Installation and skill loading

Hermes supports standalone directory plugins with a root `plugin.yaml` and `__init__.py`. After this repository is published, Hermes users can install and enable it with the standard plugin commands:

```bash
hermes plugins install MKI13/hermes-email
hermes plugins enable hermes-email
hermes plugins doctor hermes-email --ci
```

The plugin registers the read-only skill as `hermes-email:email`. Version 0.1.0 registers no tools, hooks, account integrations, or background tasks.

## Development

Hermes Email targets Python 3.11 through 3.13, matching Hermes Agent's current supported range.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest
```

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
