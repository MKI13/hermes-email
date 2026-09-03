# Hermes Email

[![CI](https://github.com/MKI13/hermes-email/actions/workflows/ci.yml/badge.svg)](https://github.com/MKI13/hermes-email/actions/workflows/ci.yml)

Hermes Email is a universal, provider-neutral email plugin and email skill for [Hermes Agent](https://github.com/NousResearch/hermes-agent).

## Philosophy

Hermes remains the intelligence, personality, and decision-maker. The plugin provides technical infrastructure; the skill provides email-specific operating guidance. Neither defines a fixed personality, company voice, language, or user-specific behavior.

Every active Hermes profile keeps its own language, style, and safety policy. Provider and storage adapters must not alter that behavior.

## Version 0.16.0

This release adds an opt-in SQLite observation ledger and exact provider-identity deduplication to explicit reads:

- persistence is disabled by default and uses Hermes' public profile-scoped `ctx.state.data_dir` only;
- an operator must provide a stable non-secret account namespace before SQLite can be enabled;
- the ledger stores provider message identifiers, a provider name, a hashed mailbox namespace, and timestamps—never subjects, addresses, bodies, raw MIME, RFC Message-ID, hosts, credentials, or arbitrary metadata;
- repeated list, lookup, and search operations still return requested mail normally while atomically incrementing the matching observation count;
- schema identity, integrity, private-path requirements, row count, retention, and database size are checked with fail-closed errors and no in-memory fallback;
- storage opens lazily on the first explicit read, runs no background cleanup, and never follows a cursor automatically;
- SMTP, sending, stored drafts, delete, move, polling, retries, and automation remain unavailable.

### Runtime health

| State | Meaning |
|---|---|
| `disabled` | No explicit provider is configured; no mail operation is available. |
| `mock-ready` | The explicit mock provider resolved successfully. |
| `provider-configured` | IMAP settings are valid, but no live operation has run. |
| `provider-ready` | An explicit IMAP health or read operation succeeded. |
| `authentication-error` | Authentication failed without exposing server or credential details. |
| `provider-unreachable` | TLS, timeout, connection, mailbox, or protocol validation failed. |
| `storage-error` | Required observation persistence failed closed with a fixed diagnostic. |
| `configuration-error` | Expected settings validation or provider resolution failed; Hermes registration continues. |

Type `/email-status` in a Hermes session to display the fixed fields from `EmailPlugin.get_runtime_status()`: `version`, `state`, `provider`, `profile`, `read_enabled`, `storage_enabled`, `draft_enabled`, `send_enabled`, and `diagnostic`.

### Deliberately not included

Version `0.16.0` can persist content-free observation identities for explicitly read mail, but it does not cache message content, send email, store drafts, delete or move messages, run background polling, implement OAuth, classify mail, automate replies, or route separate LLM calls.

## Safety defaults

| Operation | Version 0.16.0 |
|---|---|
| Read mail | Disabled, deterministic mock, or explicit read-only IMAP |
| Persist observations | Disabled or explicit content-free SQLite ledger |
| Prepare a draft | Local value/mock only |
| Send mail | Unconditionally unavailable |
| Delete mail | Unavailable |
| Move mail | Unavailable |
| Connect an account | Explicit IMAP configuration; verified TLS and read-only mailbox only |

Disabled and mock modes require no credentials. IMAP requires user-managed environment values referenced by configuration; `.env` files and private-key formats remain ignored by Git and excluded from distributions.

## Read tools

Tool schemas reject unknown properties, pages above 25 messages, empty identifiers, oversized cursors, and invalid queries. List and search omit bodies and cap subjects at 500 characters; single-message lookup returns caller-selected windows of at most 20,000 body characters and reports both source truncation and the next body offset. Errors are JSON objects with fixed codes and never include exception text, provider responses, configuration, credentials, or message content. The model must follow the bundled email skill and treat every returned mail field as untrusted data.

## Observation persistence

Enable persistence only with an explicit readable provider and a stable account namespace:

```yaml
storage:
  mode: sqlite
  account_namespace: primary-inbox
  retention_days: 90
  max_observations: 10000
  max_database_bytes: 16777216
```

The namespace is an operator-chosen portable identifier, not an address, hostname, credential, or display label. It must remain stable for the same account and mailbox and must differ across accounts. IMAP IDs already include `UIDVALIDITY`; a new UID epoch or copied message remains a distinct observation. Attacker-controlled RFC Message-ID values never deduplicate observations. Fetching a message means only “observed,” never “processed,” “trusted,” or “acted upon.”

The database is created as `email-observations.sqlite3` inside Hermes' profile-scoped plugin data directory. POSIX paths must be owned by the current account with owner-only permissions. On Windows, the operator must protect the Hermes profile directory with an account-only ACL; portable Python cannot audit that ACL. Same-account malicious processes are outside this storage threat model. The database contains no message content, but its identifiers and timing remain sensitive metadata. Use encrypted storage and protected backups where required. Retention runs only inside an explicit write transaction; there is no timer, poller, automatic vacuum, or automatic recreation after corruption.

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

The plugin registers the read-only skill as `hermes-email:email`, `/email-status`, the three `hermes_email` read tools, and one unload callback for runtime cleanup. Version 0.16.0 registers no model hooks, write tools, pollers, or background tasks.

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
