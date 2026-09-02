# Hermes Email

Hermes Email is a universal, provider-neutral email plugin and email skill for [Hermes Agent](https://github.com/NousResearch/hermes-agent).

## Philosophy

Hermes remains the intelligence, personality, and decision-maker. The plugin provides technical infrastructure; the skill provides email-specific operating guidance. Neither defines a fixed personality, company voice, language, or user-specific behavior.

A friendly German Hermes profile should produce friendly, concise German drafts. A formal English profile should preserve that profile's language and style. Provider adapters must not alter this behavior.

## Version 0.12.0

This release builds on the Hermes Agent v0.21.0 manifest and security-scan compatibility fixes in version 0.11.1 and adds bounded cursor-based pagination to the local search path:

- `plugin.yaml` targets the supported manifest v1 schema;
- optional manifest-v2-only metadata is omitted because no runtime feature depends on it;
- scanner-sensitive fixtures remain synthetic while continuing to prove redaction and unsafe-provider rejection;
- development dependencies use a pinned requirements file;
- `EmailPlugin.search_messages(query, *, limit=50, cursor=None)` fetches and searches exactly one provider page;
- search returns an `EmailMessagePage` containing only matches from that page and the provider page's unchanged `next_cursor`;
- `MAX_FETCH_LIMIT` fixes the maximum page size at 100, with no clamping;
- fetch and search share the same strict limit and opaque-cursor validation;
- no follow-up page, retry, cursor persistence, network call, tool, model hook, or background task is created automatically.

### Runtime health

| State | Meaning |
|---|---|
| `disabled` | No explicit provider is configured; no mail operation is available. |
| `mock-ready` | The explicit mock provider resolved successfully. |
| `configuration-error` | Expected settings validation or provider resolution failed; Hermes registration continues. |

Type `/email-status` in a Hermes session to display the fixed fields from `EmailPlugin.get_runtime_status()`: `version`, `state`, `provider`, `profile`, `read_enabled`, `draft_enabled`, `send_enabled`, and `diagnostic`.

### Deliberately not included

Version `0.12.0` does not connect to production mail accounts, fetch real messages, send email, delete or move messages, run background polling, implement OAuth, classify mail, automate replies, route LLM calls, or persist state in a database.

## Safety defaults

| Operation | Version 0.12.0 |
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

The plugin registers the read-only skill as `hermes-email:email`, the in-session command `/email-status`, and one official unload callback for runtime context cleanup. Version 0.12.0 registers no tools, model hooks, account integrations, or background tasks.

## Development

Hermes Email targets Python 3.11 through 3.13, matching Hermes Agent's current supported range.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
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
