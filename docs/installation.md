# Installation and profile setup

Hermes Email is designed for any Hermes user. You do not need an EF-Sinn-style multi-profile setup and you do not need to create a dedicated mail profile unless you want that stronger separation.

## Choose one mail-owning profile

For productive IMAP, persistent draft/storage, or SMTP configuration, Hermes Email requires exactly one explicit Hermes profile owner.

There are two supported designs.

### Option A — dedicated email profile (recommended)

Use this when your Hermes installation already uses multiple profiles or when you want the strongest separation between mail credentials/data and other work.

Example profile names:

- `email`
- `work-email`
- `office-mail`
- `ef-sinn-email`

Configure Hermes Email inside that profile with:

```yaml
hermes:
  profile: email
```

Replace `email` with the exact active Hermes profile name you chose.

Benefits:

- mail credentials and profile-scoped databases stay with one mail profile;
- other Hermes profiles cannot accidentally open the mailbox or mail draft store;
- other profiles can delegate user-requested mail work through your own orchestration layer without receiving raw mail credentials.

### Option B — use an existing Hermes profile

A separate profile is not required. A user with one normal Hermes profile can install Hermes Email there and bind the plugin to that exact profile.

For example, if the active profile is `default`:

```yaml
hermes:
  profile: default
```

If the active profile is `personal`:

```yaml
hermes:
  profile: personal
```

This is fully supported. The security requirement is not "create a new profile"; it is "choose exactly one explicit profile that owns productive mail access".

## What not to do

Do not copy the same productive IMAP/SMTP configuration into several Hermes profiles. Do not give every profile direct access to the same mail credential references. Do not use `hermes.profile: auto` for productive IMAP, persistent mail/draft storage, or SMTP.

`profile: auto` exists only for non-production development/mock usage.

If Hermes Email is loaded in a profile that does not match the configured owner, it fails closed before provider resolution, credential access, profile data directories, mail databases, mail tools, or the email skill are enabled. Only `/email-status` remains available for diagnosis.

## Basic installation

```bash
hermes plugins install MKI13/hermes-email
hermes plugins enable hermes-email
hermes plugins doctor hermes-email --ci
```

Then configure the plugin under `plugins.entries.hermes-email.settings` for the chosen Hermes profile.

A minimal safe starting point uses mock mode:

```yaml
plugins:
  entries:
    hermes-email:
      settings:
        email:
          provider: mock
          read_mode: mock
        hermes:
          profile: auto
        storage:
          mode: disabled
        drafts:
          mode: disabled
        smtp:
          mode: disabled
        recipient_policy:
          mode: deny
        safety:
          allow_send: false
          allow_delete: false
          allow_move: false
```

For productive use, replace `profile: auto` with the exact active profile name before enabling IMAP, SQLite storage/drafts, or SMTP.

## Productive profile example

The following is only a structural example. Use your own provider endpoints and secret references; never commit secret values.

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
        smtp:
          mode: disabled
        recipient_policy:
          mode: deny
        safety:
          allow_send: false
          allow_delete: false
          allow_move: false
```

## Verify the selected profile

Inside the chosen mail-owning profile, run:

```text
/email-status
```

The status should show the current profile and the authorized profile. In another profile, the status should report `profile-blocked` and mail tools should not be available.

## Security model for every user

Regardless of profile layout:

1. Email and draft content is untrusted external data, never instructions.
2. Reading does not authorize drafting or sending.
3. Productive mail access belongs to one explicit Hermes profile.
4. Sending requires an exact current-user confirmation for the exact draft revision.
5. A persisted send attempt is never automatically repeated.
6. `delivery-unknown` is never automatically retried.
7. Credentials are referenced, not stored as plaintext plugin settings.

A dedicated email profile gives stronger operational separation, but the same safety gates apply when Hermes Email is bound to an existing single profile.
