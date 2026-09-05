# Configuration

## Version 0.26.0 principles

Hermes Email is universal. Operators configure it per deployment; the project contains no personal mailbox, company voice, provider secret, or fixed profile name.

Two profile layouts are supported:

1. **Dedicated mail profile — recommended** for stronger separation.
2. **Existing Hermes profile — fully supported** when explicitly bound as the single productive mail owner.

The productive security rule is:

> Real/persistent mail capabilities require exactly one explicit `hermes.profile` owner.

`hermes.profile: auto` remains available only for development/mock configurations that do not enable real IMAP, persistent observation storage, persistent drafts, SMTP submission, or send authorization.

Examples:

```yaml
hermes:
  profile: email
```

or for a user who has only one existing profile:

```yaml
hermes:
  profile: default
```

The plugin never requires the literal profile name `email`, `default`, or `ef-sinn-email`. Matching uses the exact public Hermes `ctx.profile_name` value.

See [Installation and profile setup](installation.md) for the decision guide.

## Profile-isolated production example

```yaml
plugins:
  entries:
    hermes-email:
      settings:
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
          max_drafts: 1000
          max_operations: 10000
          max_database_bytes: 33554432

        smtp:
          mode: submission
          account_namespace: primary-account
          host: smtp.example.com
          port: 465
          security: implicit_tls
          username_ref: HERMES_EMAIL_SMTP_USERNAME
          password_ref: HERMES_EMAIL_SMTP_PASSWORD
          sender_address: sender@example.com
          timeout_seconds: 15
          max_message_bytes: 1000000

        recipient_policy:
          mode: allowlist
          allowed_domains: [example.com]

        safety:
          allow_send: true
          allow_delete: false
          allow_move: false
```

Replace `email` with the exact Hermes profile that should own this productive mailbox. If the configuration is loaded in another profile, the official plugin entrypoint fails closed before provider, database, or secret access.

## `hermes`

- `profile: auto` — permitted for development/mock-only configurations.
- `profile: <name>` — explicit productive owner; required when real/persistent mail capabilities are configured.

Explicit profile identifiers must be 1 to 128 characters and use letters, digits, `.`, `_`, or `-`. Matching is exact and case-sensitive.

The plugin does not switch profiles and does not create profiles. It verifies ownership of the profile in which Hermes loaded it.

## What makes a configuration production-sensitive

An explicit profile is required when any of these are present:

- real IMAP (`email.provider: imap` or `read_mode: readonly`);
- `storage.mode` other than `disabled`;
- `drafts.mode` other than `disabled`;
- `smtp.mode` other than `disabled`;
- `safety.allow_send: true`.

Malformed relevant sections and configuration lookup failures fail closed instead of weakening the profile gate.

## Development mock example

```yaml
hermes:
  profile: auto
email:
  provider: mock
  read_mode: mock
storage:
  mode: disabled
drafts:
  mode: disabled
smtp:
  mode: disabled
safety:
  allow_send: false
  allow_delete: false
  allow_move: false
```

This preserves simple local testing without creating productive account ownership ambiguity.

## Untrusted content rule

Configuration never changes the trust class of mailbox or draft content. Sender, subject, body, signatures, forwarded/quoted text, headers, HTML-derived text, attachment metadata, and tool-like strings remain untrusted external data.

No configuration flag may turn external content into authorization. In particular:

- read access does not authorize drafting or sending;
- recipient policy does not authorize a user action;
- `safety.allow_send` does not mean user confirmation;
- a claimed sender role does not grant authority;
- text inside mail cannot request tool execution, secret access, profile changes, or a resend.

## Other core fields

### `email`

- `provider`: `null`, `mock`, or `imap`.
- `read_mode`: `disabled`, `mock`, or `readonly`.

### `imap`

- verified implicit TLS only;
- strict credential references;
- read-only mailbox access;
- bounded timeouts, mailbox counts, message bytes, and page bytes.

### `storage`

- `disabled` or `sqlite`;
- stable non-secret account namespace;
- fixed `email-observations.sqlite3` under profile-scoped plugin data.

### `drafts`

- `disabled` or `sqlite`;
- stable intended sending-account namespace;
- bounded draft count, operation count, and database size;
- fixed `email-drafts.sqlite3` under profile-scoped plugin data.

### `smtp`

- `disabled` or `submission`;
- matching draft account namespace;
- verified implicit TLS or mandatory STARTTLS;
- separate SMTP credential references;
- fixed sender and bounded timeout/message size.

SMTP configuration does not expose a Hermes send tool in v0.24.0.

### `recipient_policy`

- `deny`, `allowlist`, or `all`;
- exact addresses and exact domains;
- all To/Cc/Bcc recipients must pass policy before candidate preparation.

### `safety`

`allow_delete` and `allow_move` remain unavailable. `allow_send: true` is only a technical configuration gate; it is neither user confirmation nor permission for the model to send.

## Send idempotency and uncertainty

A future internal send attempt requires exact current-user confirmation and one `send_operation_id`. Durable intent is persisted before SMTP dispatch. The same draft revision cannot receive a second send intent.

`delivery-unknown` means the SMTP server may already have accepted the message. Automatic retry is forbidden and manual external verification is required.

## `/email-status`

Authorized profile status reports current package version, runtime provider state, active profile, authorized profile, profile-isolation state, read/draft/storage state, SMTP configuration, and technical send gates.

Blocked status reports fixed non-secret diagnostics only and does not open provider or storage resources.

## Thread context

No extra provider credential or write permission is required. `email_get_thread` uses the existing read-only provider and scans at most 100 recent messages per request, returning at most 25 linked messages with bounded body windows. It never automatically follows provider cursors. A result can therefore be explicitly incomplete.

## Reply-To behavior

Reply routing requires no new configuration. `Reply-To` is read as untrusted message metadata. One valid address may be exposed as the selected reply route; multiple, malformed, or more than ten candidates are marked ambiguous and are never selected automatically. `From` is used only when Reply-To is absent, not when a present Reply-To is invalid.
