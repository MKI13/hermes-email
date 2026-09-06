# Hermes Email

[![CI](https://github.com/MKI13/hermes-email/actions/workflows/ci.yml/badge.svg)](https://github.com/MKI13/hermes-email/actions/workflows/ci.yml)

Hermes Email is a universal, provider-neutral email plugin and skill for [Hermes Agent](https://github.com/NousResearch/hermes-agent). It is designed for ordinary single-profile Hermes users as well as larger multi-profile deployments.

Hermes remains responsible for reasoning, persona, language, style, user preferences, and decisions. The plugin owns validated mail access, credential references, local persistence, profile isolation, confirmation gates, durable send intents, uncertainty recovery, and duplicate prevention.

## Version 0.44.1

Version 0.44.1 corrects multi-folder coverage when bounded or invalid address headers are omitted and separates list, search and thread scan metadata. All default access and consent gates remain unchanged.

### You do not need a dedicated email profile

A dedicated email profile is recommended for stronger operational separation, but it is not required.

Two productive layouts are supported:

1. **Dedicated mail profile — recommended**: create a profile such as `email`, `work-email`, or `office-mail` and bind Hermes Email to it.
2. **Existing profile — supported**: bind Hermes Email to the exact Hermes profile you already use, such as `default` or `personal`.

The security rule is simple: **exactly one explicit Hermes profile owns productive mail access for one deployment/account.**

Examples:

```yaml
hermes:
  profile: email
```

or:

```yaml
hermes:
  profile: default
```

The name `ef-sinn-email` used in project examples is only an example of a dedicated profile. Nothing in Hermes Email is hard-coded for EF-Sinn or any particular organization.

See [Installation and profile setup](docs/installation.md) for both supported layouts.

## Attachment metadata only

Message and thread detail may expose a bounded `attachments` list. Each item contains only an opaque message-local attachment ID, bounded filename, MIME type, optional decoded size, and disposition. Every item carries `metadata_is_untrusted: true`, `content_available: false`, and `authorization: none`.

Hermes Email v0.44.1 does **not** download, save, open, render, scan, execute, forward, or upload attachment content. List/search summaries intentionally omit attachment metadata. Attachment filenames and MIME declarations are external input and must never be treated as trusted paths, commands, or proof of file type.

## Deterministic sender classification

Operators may configure `classification` rules for `internal`, `customer`, and `supplier` senders. Exact address rules take precedence over domain rules; anything unmatched is `unknown-external`. Conflicting exact rules are rejected at configuration load.

Classification is informational only. Every returned `sender_classification` includes `authorization: none`; sender identity or category can never authorize tools, drafting, sending, secret access, or policy changes.

## Safe Reply-To routing

Message detail and thread results now include a bounded `reply_route`. A single syntactically valid `Reply-To` address is selected ahead of `From`; when no `Reply-To` exists, the validated `From` sender is used. Multiple, invalid, or oversized `Reply-To` sets are marked ambiguous and receive no automatic selection.

`reply_route.authorization` is always `none`. The route is untrusted metadata only: it cannot create a draft, choose a recipient for sending, confirm a send, or trigger any tool. Any external action still requires the current user's direct request and the existing draft/confirmation/send gates.

## Prompt-injection boundary

Every mailbox and draft field is **untrusted external data**. This includes sender names, addresses, subjects, message bodies, signatures, forwarded text, quoted replies, HTML-derived text, headers, attachment metadata, and tool-like text embedded in a message.

External mail content has **zero action authority**. It cannot authorize Hermes to:

- run another tool;
- reveal or resolve secrets;
- change safety rules or profiles;
- create, mutate, confirm, or send a draft;
- forward/reply/contact another recipient;
- bypass recipient policy or duplicate prevention;
- retry a `delivery-unknown` send;
- treat a claimed administrator, CEO, support agent, system message, or security warning as current-user authorization.

Reading a mail authorizes only the requested read operation. Any later action must be independently justified by the **current user's direct request** and governing Hermes policy.

CI now locks this trust contract so the untrusted-content markers and core prompt-injection rules cannot be silently removed without failing tests.

## Exclusive productive profile isolation

Production mail capabilities must be owned by one explicit Hermes profile. The official plugin entrypoint evaluates profile ownership before provider resolution, SQLite construction, secret access, mail-tool registration, or skill registration.

If the active profile does not exactly match the configured owner:

- no IMAP provider is created;
- no observation or draft database is opened or created;
- no mail credential is resolved;
- no email read or draft tool is registered;
- the email skill is not registered;
- SMTP/send gates remain unavailable;
- only `/email-status` is registered so the operator can see that access was blocked.

`hermes.profile: auto` is accepted only for development/mock configurations. As soon as real IMAP, SQLite observation storage, SQLite drafts, SMTP submission, or `safety.allow_send` is configured, an explicit profile owner is required.

## Safety model

| Operation | Version 0.44.1 |
|---|---|
| Productive profile ownership | Exact explicit profile required |
| Dedicated mail profile | Recommended, not required |
| Existing single profile | Fully supported when explicitly bound |
| Development mock mode | `hermes.profile: auto` allowed |
| Mail/draft/attachment metadata trust | Untrusted data; zero action authority |
| Read mail | Disabled, mock, or explicit read-only IMAP |
| Persist observations | Disabled or profile-scoped content-free SQLite |
| Store drafts | Disabled or profile-scoped local SQLite |
| Confirm send candidate | Trusted current-user proof for exact draft revision |
| Persist send intent | Durable SQLite record before SMTP dispatch |
| Recover interrupted send | Prior-process dispatch becomes `delivery-unknown` |
| Retry `delivery-unknown` | Never automatically |
| Send through Hermes tools | Unavailable |
| Attachment content access/open/execute | Unavailable |
| Delete/move/purge/poll/auto-reply | Unavailable |

Email text, quoted instructions, signatures, senders, draft content, model output, configuration, SMTP readiness, recipient policy, or `safety.allow_send` can never substitute for current-user authorization.

## Productive profile example

```yaml
hermes:
  profile: email

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

classification:
  internal_domains: [mycompany.example]
  customer_domains: [customer.example]
  supplier_domains: [supplier.example]

safety:
  allow_send: true
  allow_delete: false
  allow_move: false
```

Replace `email` with the exact active Hermes profile that should own productive mail access. SMTP remains disconnected from the Hermes model-facing runtime in this release. `allow_send: true` only arms technical eligibility for internal future send orchestration; it does not create a send tool and is not user confirmation.

## Optional multi-folder IMAP

Set `imap.mailboxes` to your exact permitted folders (up to eight) and set
`imap.account_namespace`. Existing list/search/thread tools then operate across
those folders using account-and-folder-bound message IDs. List/search fetch
headers only; this mode does not perform full-text body search. Thread building
loads only linked message bodies and explicitly reports incomplete coverage,
missing folders, truncated headers and missing messages. All reads remain
read-only; mail content never grants authority.

See [Multi-folder behavior, limits and migration](docs/multi-folder.md) and
`examples/config.multi-folder.example.yaml`. Default single-folder behavior is
unchanged. With only one Hermes profile, use `hermes.profile: default`; a dedicated
mail profile is optional. The authenticated Proton test-account gate remains open.

## Read tools

When the authorized profile has a readable provider, Hermes can expose:

- `email_list_messages`
- `email_get_message`
- `email_search_messages`
- `email_get_thread` — bounded RFC-header-derived conversation context

List/search results omit bodies. Reads are bounded, use read-only provider behavior, and mark returned mail fields as untrusted external content.


### Thread context

`email_get_thread` reconstructs a bounded chronological conversation from RFC `Message-ID`, `In-Reply-To`, and `References` relationships. It never groups messages merely because subject, sender, body text, or wording looks similar. The result explicitly reports whether the provider scan was complete, whether the returned thread was truncated, and how many referenced message IDs were not present in the scanned window. All thread messages remain untrusted external content and gain no action authority by being part of a thread.

### Safe Reply-All drafts

`email_create_reply_all_draft` is available only when local drafts, message lookup, and `reply_policy.own_addresses` are configured. The validated Reply-To/From route becomes the primary To recipient. Original message recipients that are neither the configured own identities nor duplicates become Cc. Incoming To and Cc are preserved separately; Reply-All deliberately places additional non-self To/Cc recipients in Cc. Invalid or incomplete recipient headers block automatic Reply-All. Bcc is always empty. The source message body is never copied.

### Send review without sending

`email_review_draft_send` is a read-only preflight for an active local draft. It returns exact recipients including Bcc, current revision, subject, body character count, deployment recipient-policy decisions, and runtime readiness. It deliberately does not include the body. It cannot confirm a draft, create a send intent, contact SMTP, or send mail. `confirmation_required` is always true, `confirmation_present` is false, `send_available` is false, and `authorization` is none.

## Local draft tools

When the authorized profile enables local draft SQLite, Hermes can expose:

- `email_create_draft`
- `email_create_reply_draft` — local reply draft from one user-selected source message; never sends
- `email_list_drafts`
- `email_get_draft`
- `email_update_draft`
- `email_trash_draft`
- `email_restore_draft`

Draft mutations require opaque operation IDs and exact revisions. A revision conflict must be reviewed instead of overwritten. Drafts are local plugin records, not provider mailbox drafts. Draft content remains untrusted data and cannot authorize another action.

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

Read [Installation and profile setup](docs/installation.md) before enabling productive IMAP, persistent storage/drafts, or SMTP.

Hermes Email targets Python 3.11 through 3.13.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pytest
python -m build
python scripts/check_dist.py
```

CI tests Python 3.11, 3.12, and 3.13, validates the built wheel/sdist, imports the clean wheel, and runs Hermes Plugin Doctor against the pinned Hermes Agent compatibility target. Profile-isolation tests verify blocked profiles cannot reach mail infrastructure. Prompt-injection contract tests verify that model-facing mail content remains explicitly untrusted and that external content cannot become action authorization.

## Documentation

- [Installation and profile setup](docs/installation.md)
- [Architecture](docs/architecture.md)
- [Configuration](docs/configuration.md)
- [Security model](docs/security-model.md)
- [Hermes compatibility](docs/official-hermes-compatibility.md)
- [Contributing](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md)

## License

[MIT](LICENSE)


## Reliable operational audit

With `audit.mode: sqlite`, tool results include an `audit` receipt indicating whether the event was recorded. A failed audit write is a visible warning, not proof that the mail operation failed. Reuse the original draft `operation_id` only for the identical request. Do not create a replacement operation merely because an audit warning appeared.

See [Audit reliability and upgrade guidance](docs/audit-reliability.md). This optional log is not the mandatory send-intent ledger and does not confer consent or send authority.

## Local human approval

See [Trusted local approval and its limits](docs/trusted-approval.md). The `email-approve` CLI command is not a model tool and never sends. Gateway approvals remain unavailable.

## Complete reply headers

See [Reply header continuity and draft migration](docs/reply-headers.md).

## Human-reviewed local test sending

The opt-in `email-send` terminal command is separate from model tools. It requires a fresh exact human review. See [Workflow configuration and limits](docs/reviewed-send.md); the default remains disabled.

## Provider/account validation

The independent GreenMail test exercises real TLS IMAP/SMTP accounts and a reviewed
send end to end. Actual Proton account verification remains pending: transport
checks without credentials do not establish a working mailbox. `account-test` mode
requires an exact-address-only allowlist and a new local human confirmation for
each attempt. No account or sending mode is enabled by an upgrade.

See [Provider validation, evidence and remaining gates](docs/provider-validation.md).
