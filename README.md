# Hermes Email

[![CI](https://github.com/MKI13/hermes-email/actions/workflows/ci.yml/badge.svg)](https://github.com/MKI13/hermes-email/actions/workflows/ci.yml)

Hermes Email is a universal, provider-neutral email plugin and email skill for [Hermes Agent](https://github.com/NousResearch/hermes-agent).

## Philosophy

Hermes remains the intelligence, personality, and decision-maker. The plugin provides technical infrastructure; the skill provides email-specific operating guidance. Neither defines a fixed personality, company voice, language, or user-specific behavior.

A friendly German Hermes profile should produce friendly, concise German drafts. A formal English profile should preserve that profile's language and style. Provider adapters must not alter this behavior.

## Version 0.14.0

This release adds the first production transport: bounded, read-only IMAP over verified implicit TLS.

- IMAP credentials remain validated references and resolve only for an explicit health, fetch, or lookup operation;
- every connection uses certificate and hostname verification with TLS 1.2 or newer;
- mailboxes open with IMAP `EXAMINE`, and every message request uses UID `BODY.PEEK` partial fetches;
- UID cursors are caller-driven, strictly decreasing, bound to `UIDVALIDITY`, and never followed automatically;
- MIME is parsed as untrusted data, attachments are omitted, and HTML becomes bounded plain text without scripts, attributes, links, images, or remote access;
- provider health is explicit and redacted; registration and `/email-status` never connect;
- SMTP, sending, tools, polling, persistence, and automation remain unavailable.

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

Version `0.14.0` can read one explicitly configured IMAP mailbox, but it does not send email, store provider drafts, delete or move messages, run background polling, implement OAuth, classify mail, automate replies, register model tools, route LLM calls, or persist state in a database.

## Safety defaults

| Operation | Version 0.14.0 |
|---|---|
| Read mail | Disabled, deterministic mock, or explicit read-only IMAP |
| Prepare a draft | Local value/mock only |
| Send mail | Unconditionally unavailable |
| Delete mail | Unavailable |
| Move mail | Unavailable |
| Connect an account | Explicit IMAP configuration; verified TLS and read-only mailbox only |

Disabled and mock modes require no credentials. IMAP requires user-managed environment values referenced by configuration; `.env` files and private-key formats remain ignored by Git and excluded from distributions.

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

The plugin registers the read-only skill as `hermes-email:email`, the in-session command `/email-status`, and one official unload callback for runtime cleanup. Version 0.14.0 registers no model tools, hooks, pollers, or background tasks. IMAP access is currently available through the Python facade; Hermes-facing read tools are the next release milestone.

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
