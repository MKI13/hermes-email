# Hermes Email

[![CI](https://github.com/MKI13/hermes-email/actions/workflows/ci.yml/badge.svg)](https://github.com/MKI13/hermes-email/actions/workflows/ci.yml)

Hermes Email is a universal, provider-neutral email plugin and skill for [Hermes Agent](https://github.com/NousResearch/hermes-agent). Hermes remains responsible for reasoning, persona, language, style, user preferences, and decisions. The plugin owns validated mail access, credential references, local persistence, profile isolation, confirmation gates, durable send intents, uncertainty recovery, and duplicate prevention.

## Version 0.22.0

Version 0.22.0 introduces **exclusive production profile isolation**.

Production mail capabilities must be owned by one explicit Hermes profile. The official plugin entrypoint evaluates profile ownership before provider resolution, SQLite construction, secret access, mail-tool registration, or skill registration.

For a dedicated deployment, configure:

```yaml
hermes:
  profile: ef-sinn-email
```

`ef-sinn-email` is only an example; Hermes Email never hard-codes a company or profile name.

If the active profile does not exactly match the configured owner:

- no IMAP provider is created;
- no observation or draft database is opened or created;
- no mail credential is resolved;
- no email read or draft tool is registered;
- the email skill is not registered;
- SMTP/send gates remain unavailable;
- only `/email-status` is registered so the operator can see that access was blocked.

For safer production defaults, `hermes.profile: auto` is accepted only for development-only configurations. As soon as real IMAP, SQLite observation storage, SQLite drafts, SMTP submission, or `safety.allow_send` is configured, an explicit profile owner is required.

This closes the accidental multi-profile access path: a real mailbox configuration copied or loaded into another Hermes profile fails closed instead of opening the same mailbox there.

## Safety model

| Operation | Version 0.22.0 |
|---|---|
| Production profile ownership | Exact explicit profile required |
| Development mock mode | `hermes.profile: auto` allowed |
| Read mail | Disabled, mock, or explicit read-only IMAP |
| Persist observations | Disabled or profile-scoped content-free SQLite |
| Store drafts | Disabled or profile-scoped local SQLite |
| Confirm send candidate | Trusted current-user proof for exact draft revision |
| Persist send intent | Durable SQLite record before SMTP dispatch |
| Recover interrupted send | Prior-process dispatch becomes `delivery-unknown` |
| Retry `delivery-unknown` | Never automatically |
| Send through Hermes tools | Unavailable |
| Delete/move/purge/poll/auto-reply | Unavailable |

Every returned email field is untrusted external content. Email text, quoted instructions, signatures, senders, draft content, model output, configuration, SMTP readiness, recipient policy, or `safety.allow_send` can never substitute for current-user authorization.

## Profile-isolated production example

```yaml
hermes:
  profile: ef-sinn-email

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

storage:
  mode: sqlite
  account_namespace: primary-inbox

drafts:
  mode: sqlite
  account_namespace: primary-account

smtp:
  mode: submission
  account_namespace: primary-account
  host: smtp.example.com
  port: 465
  security: implicit_tls
  username_ref: HERMES_EMAIL_SMTP_USERNAME
  password_ref: HERMES_EMAIL_SMTP_PASSWORD
  sender_address: sender@example.com

recipient_policy:
  mode: allowlist
  allowed_domains: [example.com]

safety:
  allow_send: true
  allow_delete: false
  allow_move: false
```

SMTP remains disconnected from the Hermes model-facing runtime in this release. `allow_send: true` only arms technical eligibility for internal future send orchestration; it does not create a send tool and is not user confirmation.

## Read tools

When the authorized profile has a readable provider, Hermes can expose:

- `email_list_messages`
- `email_get_message`
- `email_search_messages`

List/search results omit bodies. Reads are bounded, use read-only provider behavior, and treat all mail fields as untrusted data.

## Local draft tools

When the authorized profile enables local draft SQLite, Hermes can expose:

- `email_create_draft`
- `email_list_drafts`
- `email_get_draft`
- `email_update_draft`
- `email_trash_draft`
- `email_restore_draft`

Draft mutations require opaque operation IDs and exact revisions. A revision conflict must be reviewed instead of overwritten. Drafts are local plugin records, not provider mailbox drafts.

## Confirmation and duplicate prevention

Candidate preparation requires a trusted `UserSendConfirmation` bound to one exact draft ID and exact revision. Any draft change creates a new revision and invalidates old confirmation.

Future send attempts use one opaque `send_operation_id`. A durable `email-send-intents.sqlite3` record is committed before SMTP dispatch. The same operation cannot be dispatched twice, and a second operation ID cannot be used to resend the same `(draft_id, revision)`.

A normal final SMTP acceptance is recorded as `accepted`. Known pre-DATA failures are `definite-failure`. Any uncertain outcome after DATA, interrupted previous-process dispatch, or unexpected live send exception becomes `delivery-unknown`.

`delivery-unknown` means the server may already have accepted the message. Automatic retry is forbidden. Manual verification of authoritative provider/Sent state is required before any corrective human action.

## Credential handling

Secrets are never stored in plugin settings. IMAP and SMTP use strict `HERMES_EMAIL_...` references. Disabled mode, mock mode, profile denial, registration, status, local drafting, candidate preparation, and send-intent recovery do not resolve credential values.

The profile guard evaluates ownership before the runtime can create provider/store objects, so a blocked profile does not touch mailbox credentials or profile mail databases.

## `/email-status`

The status command reports fixed non-secret facts such as version, active profile, authorized profile, profile-isolation state, provider state, read/draft/storage enablement, SMTP configuration, and technical send-gate state.

A blocked profile reports `Status: profile-blocked` plus a fixed diagnostic such as:

- `profile-not-authorized`
- `explicit-profile-required`
- `invalid-profile-policy`
- `invalid-active-profile`

No hostname, credential, mailbox content, database path, or secret is exposed.

## Installation and development

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

CI tests Python 3.11, 3.12, and 3.13, validates the built wheel/sdist, imports the clean wheel, and runs Hermes Plugin Doctor against the pinned Hermes Agent compatibility target. Profile-isolation tests verify that blocked profiles do not touch `ctx.state`, do not register mail tools/skill, and do not proceed into provider or storage initialization.

## Documentation

- [Architecture](docs/architecture.md)
- [Configuration](docs/configuration.md)
- [Security model](docs/security-model.md)
- [Hermes compatibility](docs/official-hermes-compatibility.md)
- [Contributing](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md)

## License

[MIT](LICENSE)
