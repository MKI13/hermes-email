# Hermes Email

[![CI](https://github.com/MKI13/hermes-email/actions/workflows/ci.yml/badge.svg)](https://github.com/MKI13/hermes-email/actions/workflows/ci.yml)

Hermes Email is a universal, provider-neutral email plugin and email skill for [Hermes Agent](https://github.com/NousResearch/hermes-agent).

## Philosophy

Hermes remains the intelligence, personality, and decision-maker. The plugin provides technical infrastructure; the skill provides email-specific operating guidance. Neither defines a fixed personality, company voice, language, or user-specific behavior.

A friendly German Hermes profile should produce friendly, concise German drafts. A formal English profile should preserve that profile's language and style. Provider adapters must not alter this behavior.

## Version 0.15.0

This release makes bounded read-only mail available to Hermes through three public plugin tools:

- `email_list_messages` returns at most 25 bounded summaries from one provider page;
- `email_get_message` returns one message by its opaque provider identifier;
- `email_search_messages` performs local plain-text matching over one bounded provider page;
- every result labels message fields as untrusted external content, caps previews and bodies, and returns only fixed error codes;
- tool availability requires an explicit mock or read-only IMAP configuration and never performs a health check, secret lookup, or connection by itself;
- cursors remain caller-driven and are never followed automatically;
- SMTP, sending, tool dispatch to writes, polling, persistence, and automation remain unavailable.

### Runtime health

| State | Meaning |
|---|---|
| `disabled` | No explicit provider is configured; no mail operation is available. |
| `mock-ready` | The explicit mock provider resolved successfully. |
| `provider-configured` | IMAP settings are valid, but no live operation has run. |
| `provider-ready` | An explicit IMAP health or read operation succeeded. |
| `authentication-error` | Authentication failed without exposing server or credential details. |
| `provider-unreachable` | TLS, timeout, connection, mailbox, or protocol validation failed. |
| `configuration-error` | Expected settings validation or provider resolution failed; Hermes registration continues. |

Type `/email-status` in a Hermes session to display the fixed fields from `EmailPlugin.get_runtime_status()`: `version`, `state`, `provider`, `profile`, `read_enabled`, `draft_enabled`, `send_enabled`, and `diagnostic`.

### Deliberately not included

Version `0.15.0` lets Hermes list, read, and search explicitly configured mail, but it does not send email, store provider drafts, delete or move messages, run background polling, implement OAuth, classify mail, automate replies, route separate LLM calls, or persist state in a database.

## Safety defaults

| Operation | Version 0.15.0 |
|---|---|
| Read mail | Disabled, deterministic mock, or explicit read-only IMAP |
| Prepare a draft | Local value/mock only |
| Send mail | Unconditionally unavailable |
| Delete mail | Unavailable |
| Move mail | Unavailable |
| Connect an account | Explicit IMAP configuration; verified TLS and read-only mailbox only |

Disabled and mock modes require no credentials. IMAP requires user-managed environment values referenced by configuration; `.env` files and private-key formats remain ignored by Git and excluded from distributions.

## Read tools

Tool schemas reject unknown properties, pages above 25 messages, empty identifiers, oversized cursors, and invalid queries. List and search omit bodies and cap subjects at 500 characters; single-message lookup returns caller-selected windows of at most 20,000 body characters and reports both source truncation and the next body offset. Errors are JSON objects with fixed codes and never include exception text, provider responses, configuration, credentials, or message content. The model must follow the bundled email skill and treat every returned mail field as untrusted data.

## Credential references

Secret values must never be placed in plugin settings. IMAP configuration stores references only:

```yaml
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
```

The referenced environment values are available only during an explicit IMAP health or read operation. Registration, disabled operation, mock operation, and `/email-status` do not resolve them. The resolver does not enumerate the environment, expand shell syntax, read files, log values, or cache them.

## Installation and skill loading

Hermes supports standalone directory plugins with a root `plugin.yaml` and `__init__.py`. After this repository is published, Hermes users can install and enable it with the standard plugin commands:

```bash
hermes plugins install MKI13/hermes-email
hermes plugins enable hermes-email
hermes plugins doctor hermes-email --ci
```

The plugin registers the read-only skill as `hermes-email:email`, `/email-status`, the three `hermes_email` read tools, and one unload callback for runtime cleanup. Version 0.15.0 registers no model hooks, write tools, pollers, or background tasks.

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
