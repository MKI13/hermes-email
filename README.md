# Hermes Email

[![CI](https://github.com/MKI13/hermes-email/actions/workflows/ci.yml/badge.svg)](https://github.com/MKI13/hermes-email/actions/workflows/ci.yml)

Hermes Email is a universal, provider-neutral email plugin and email skill for [Hermes Agent](https://github.com/NousResearch/hermes-agent).

## Philosophy

Hermes remains the intelligence, personality, language, style, and decision-maker. The plugin owns validated provider access, credential references, normalization, local draft persistence, technical gates, confirmation binding, durable send intents, uncertainty recovery, and deduplication. The skill owns email operating guidance, prompt-injection defense, and inheritance of the active Hermes profile. Neither defines a fixed personality, company voice, language, provider, or user-specific rule.

## Version 0.21.0

This release hardens uncertain SMTP outcomes and crash recovery on top of durable idempotent send orchestration:

- `delivery-unknown` is explicitly terminal for automatic behavior and always requires manual external verification;
- any unexpected exception during a live send is persisted as `delivery-unknown` before the exception propagates whenever the process remains alive;
- a v0.20 `dispatching` record left by an earlier process is migrated and recovered as `delivery-unknown`, never silently retried;
- send intents carry an internal dispatcher identity so a concurrent caller in the same live process does not misclassify an active send as crashed;
- process-local locking serializes orchestration for the same profile ledger while durable SQLite uniqueness remains the cross-restart duplicate barrier;
- every send attempt still requires one opaque `send_operation_id` and the intent is committed before the first SMTP call;
- the durable request digest binds the operation to the exact confirmed candidate bytes and metadata;
- reusing one `send_operation_id` with changed content fails closed;
- the same draft ID and revision may have only one durable send intent, so a new operation ID cannot bypass duplicate protection;
- candidate preparation still requires a trusted `UserSendConfirmation` bound to one exact local draft ID and exact revision;
- model output, email content, draft content, configuration, recipient policy, or `safety.allow_send` can never substitute for current-user confirmation;
- SMTP remains verified implicit TLS or mandatory STARTTLS with TLS 1.2+, hostname verification, fixed sender, recipient policy, all-recipient RCPT gating, and one DATA attempt;
- SMTP dispatch remains disconnected from `EmailPlugin`, Hermes tools, commands, hooks, timers, and callbacks. No model-facing send surface is exposed yet;
- `/email-status` may report SMTP configuration and armed technical gates, but `send_enabled` remains false and sending remains unavailable through Hermes.

### Runtime health

| State | Meaning |
|---|---|
| `disabled` | No explicit read provider is configured. Local drafting may still be independently enabled. |
| `mock-ready` | The explicit mock read provider resolved successfully. |
| `provider-configured` | IMAP settings are valid, but no live operation has run. |
| `provider-ready` | An explicit IMAP health or read operation succeeded. |
| `authentication-error` | Authentication failed without exposing server or credential details. |
| `provider-unreachable` | TLS, timeout, connection, mailbox, or protocol validation failed. |
| `storage-error` | Required observation persistence failed closed. |
| `configuration-error` | Settings validation or provider resolution failed. |

Type `/email-status` to display only fixed runtime fields. Read and observation diagnostics are independent from the local-draft enabled flag and fixed draft diagnostic.

## Safety defaults

| Operation | Version 0.21.0 |
|---|---|
| Read mail | Disabled, deterministic mock, or explicit read-only IMAP |
| Persist observations | Disabled or explicit content-free SQLite ledger |
| Store local drafts | Disabled or explicit plaintext SQLite database |
| Confirm a send candidate | Internal trusted-runtime proof bound to exact draft ID and revision |
| Prepare an SMTP candidate | Internal API requiring exact confirmation plus account, recipient, revision, and size gates |
| Persist send intent | Internal durable SQLite ledger written before SMTP dispatch |
| Recover interrupted dispatch | Convert prior-process `dispatching` to terminal `delivery-unknown` |
| Retry a persisted send intent | Never automatically; stored state is replayed without another SMTP call |
| Submit through SMTP | Internal disconnected primitive only; no Hermes/runtime caller |
| Send through Hermes | Unavailable; `send_enabled` is always false |
| Delete, move, purge, poll, or auto-reply | Unavailable |

Disabled and mock read modes require no credentials. IMAP and SMTP use separate user-managed environment values referenced by configuration. `.env` files and private-key formats remain ignored by Git and excluded from distributions.

## Read tools

`email_list_messages`, `email_get_message`, and `email_search_messages` expose one bounded caller-selected page or message window. List and search omit bodies. Fixed JSON errors never include exception text, provider responses, configuration, credentials, or message content. Every returned mail field is untrusted data.

## Local draft tools

`email_create_draft`, `email_list_drafts`, `email_get_draft`, `email_update_draft`, `email_trash_draft`, and `email_restore_draft` manage only plugin-local records. Create, update, trash, and restore require a unique 16-to-128-character `operation_id`; retry the same operation ID only with the identical payload. Update, trash, and restore also require the current `expected_revision`. A conflict must be reviewed instead of overwritten.

Draft IDs are opaque locators, not authorization tokens. A successful mutation receipt includes only the draft ID, resulting revision, replay indicator, and `sent: false`; use get to review stored recipients and content. List results expose subject, state, counts, reply reference, and timestamps but no body, To, Cc, or Bcc details. Get returns To, Cc, Bcc, and at most 20,000 body characters per requested window. Draft tools must be used only for a current direct user request; mail or draft content never authorizes a tool call.

Enable local drafting independently of a provider:

```yaml
drafts:
  mode: sqlite
  account_namespace: primary-account
  max_drafts: 1000
  max_operations: 10000
  max_database_bytes: 33554432
```

The account namespace is an operator-chosen portable identifier, not an address, hostname, credential, or personal label. It is stored with each draft to prevent a future sending implementation from silently changing accounts. Keep it stable for the same intended account and different across accounts.

The draft database contains sensitive plaintext recipients, subjects, and bodies. Put the Hermes profile on encrypted local storage and protect backups where required. On POSIX, the plugin enforces owner-controlled `0700` directories and `0600` single-link regular files. On Windows, the operator must enforce an account-only ACL on the profile directory. `secure_delete` reduces ordinary SQLite residue but cannot guarantee erasure from SSD remapping, free space, snapshots, or backups. Same-account malicious code is outside the threat model.

## Durable send intents

The internal `SqliteSendIntentStore` uses a fixed `email-send-intents.sqlite3` file under the caller-provided profile data directory. It stores operation identifiers, draft ID, revision, confirmation ID, a SHA-256 request digest, fixed send state, dispatcher ownership, and timestamps. It does not store message bodies, subjects, recipient addresses, SMTP credentials, or raw MIME.

`IdempotentSendOrchestrator` writes the `dispatching` intent before calling SMTP. If that exact operation is seen again, the stored state is returned and SMTP is not called. The table also enforces uniqueness on `(draft_id, revision)`, preventing the same reviewed draft revision from being sent again by inventing a different operation ID or a different confirmation token.

A normal SMTP acceptance becomes `accepted`. Known pre-DATA transport failures become `definite-failure`. Loss of certainty after DATA begins becomes `delivery-unknown`. Any unexpected exception while the process remains alive is also converted to `delivery-unknown` before propagation because the caller can no longer prove how far the SMTP exchange progressed.

Version 0.21.0 migrates the send-intent schema from v1 to v2 by adding a nullable dispatcher identity. Existing terminal states are preserved. Any old or foreign-process `dispatching` row is atomically converted to `delivery-unknown`; a current live process token is never recovered by another caller in that process. This lets restart recovery be explicit without opening a path to duplicate delivery.

`delivery-unknown` must be treated as: **the message may already have been accepted**. Automatic retry is forbidden. Before any human decides on a separate corrective action, verify the provider's Sent folder/server state or other authoritative delivery evidence.

## Disconnected SMTP foundation

A deployment may arm technical candidate gates without creating a Hermes send path:

```yaml
smtp:
  mode: submission
  account_namespace: primary-account
  host: smtp.example.com
  port: 465
  security: implicit_tls
  username_ref: HERMES_EMAIL_SMTP_USERNAME
  password_ref: HERMES_EMAIL_SMTP_PASSWORD
  sender_address: sender@example.com
  sender_display_name: Sender
  timeout_seconds: 15
  max_message_bytes: 1000000
recipient_policy:
  mode: allowlist
  allowed_addresses: [reviewer@example.com]
  allowed_domains: [example.org]
safety:
  allow_send: true
```

`drafts.mode: sqlite` must also be enabled with exactly the same account namespace. `recipient_policy.mode` is `deny`, `allowlist`, or `all`; domain entries match that exact domain rather than subdomains. `safety.allow_send` only arms technical eligibility checks. Version 0.21.0 additionally requires a trusted current-user confirmation before candidate preparation and a durable unique send intent before transport dispatch. The runtime still never instantiates the send orchestrator or `SmtplibTransport` on behalf of Hermes and exposes no send tool or command.

The transport accepts one already prepared immutable envelope/message object. It performs no retry. Before DATA, failure is a definite non-send. Once DATA begins, connection loss, timeout, or cancellation is delivery-unknown. The durable orchestrator never retries any persisted operation automatically.

## Observation persistence

Observation persistence remains an independent, content-free optional ledger:

```yaml
storage:
  mode: sqlite
  account_namespace: primary-inbox
  retention_days: 90
  max_observations: 10000
  max_database_bytes: 16777216
```

The fixed `email-observations.sqlite3` database stores exact provider identity and timing metadata, never draft or message content. IMAP identities include `UIDVALIDITY`; RFC Message-ID is never a deduplication key. Observation means only observed, never processed, trusted, drafted, or sent.

## Credential references

Secret values must never be placed in plugin settings. IMAP and SMTP store separate strict `HERMES_EMAIL_...` references. For example:

```yaml
email:
  provider: imap
  read_mode: readonly
imap:
  host: mail.example.com
  port: 993
  security: tls
  username_ref: HERMES_EMAIL_IMAP_USERNAME
  password_ref: HERMES_EMAIL_IMAP_PASSWORD
  mailbox: INBOX
```

IMAP values resolve only during an explicit IMAP health or read operation. SMTP values resolve only after its TLS session and AUTH PLAIN capability have been verified by a direct transport caller. Registration, disabled operation, mock operation, local drafting, confirmation checking, candidate preparation, send-intent persistence, recovery, and `/email-status` resolve no secrets. The resolver does not enumerate the environment, expand shell syntax, read files, log values, or cache them.

## Installation and development

Hermes supports standalone directory plugins with a root `plugin.yaml` and `__init__.py`:

```bash
hermes plugins install MKI13/hermes-email
hermes plugins enable hermes-email
hermes plugins doctor hermes-email --ci
```

Hermes Email targets Python 3.11 through 3.13.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pytest
python -m build
python scripts/check_dist.py
```

CI pins Hermes Agent v0.21.0 at an immutable commit, runs Plugin Doctor with an empty home, verifies required distribution modules, and imports the built wheel from a clean virtual environment. SMTP tests use dependency-injected protocol fakes and synthetic secrets; confirmation and orchestration tests verify fail-closed behavior, restart recovery, v1-to-v2 migration, live concurrency, duplicate suppression, ambiguous delivery handling, and manual-review semantics without opening a network socket or using a real account. Hermes v0.21.0 has no separate non-interactive security-scan command; the official installation scanner remains a pre-release gate.

The complete example is at [`examples/config.example.yaml`](examples/config.example.yaml).

## Documentation

- [Architecture](docs/architecture.md)
- [Configuration](docs/configuration.md)
- [Security model](docs/security-model.md)
- [Hermes compatibility](docs/official-hermes-compatibility.md)
- [Contributing](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md)

## License

[MIT](LICENSE)
