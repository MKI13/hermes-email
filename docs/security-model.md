# Security Model

## Version 0.1.0 boundary

This release is a foundation, not a mail client. It performs no network access, account authentication, mailbox polling, message transmission, deletion, or movement.

## Safe by default

- Reading is `disabled` by default and has no production implementation.
- Draft preparation creates only a local `EmailDraft` value.
- `EmailPlugin.send_message()` always raises `SendingUnavailableError`.
- Provider capabilities default to false.
- Sending, deletion, and movement configuration flags default to false.
- No credentials are required or included.

A future provider capability and a user safety setting are separate checks. Supporting an operation must never imply permission to perform it.

## Trust boundaries

### Email content

Messages, headers, attachments, and quoted text are untrusted input. They may contain prompt injection, deceptive instructions, malicious links, or sensitive data. The skill instructs Hermes to treat them as content rather than agent authority.

### Providers

Future provider adapters run as trusted plugin code with the user's process permissions. Each adapter must minimize credential access, validate remote data at the provider boundary, use secure transport defaults, and avoid logging message bodies or secrets.

### Hermes context

The plugin may consume owned snapshots from stable public Hermes APIs. It must not serialize live Hermes objects, scrape private internal files, or replace missing context with a plugin-defined personality.

## Future side-effect requirements

Before any release can send, delete, or move mail, it must include:

1. an explicit operation-specific configuration gate;
2. a runtime authorization check independent of provider capability;
3. clear user-visible preview and confirmation semantics;
4. idempotency and deduplication behavior;
5. audit logging that redacts secrets and minimizes content;
6. focused tests for denial, failure, retries, and ambiguous state;
7. updated security documentation and changelog.

No item in this list is implemented implicitly by the version 0.1.0 interfaces.

## Credentials and logs

Never commit tokens, passwords, private keys, account exports, or real messages. `.env`, `.env.*`, `*.pem`, and `*.key` are ignored. Future logs should record bounded operational metadata and opaque identifiers, not message bodies or credentials.
