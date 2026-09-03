# Security Policy

## Supported versions

The `0.x` series is an early foundation. Security fixes are applied to the latest release.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability or exposed secret. Use GitHub's private security advisory flow for this repository. Include the affected version, impact, reproduction steps, and any suggested mitigation.

## Operational safety

Version `0.18.0` adds a disconnected SMTP transport primitive and pure exact-revision candidate gates. SMTP and recipient authorization default to disabled and deny; submission requires matching draft/account identity, a fixed ASCII sender, separate secret references, verified implicit TLS or mandatory STARTTLS, TLS 1.2 or newer, system trust, hostname verification, AUTH PLAIN after TLS, and bounded time and message size. Candidate MIME is deterministic plain text with no Bcc header, HTML, attachments, custom headers, SMTPUTF8, or automatic reply identity. Every RCPT must succeed before one DATA call; failures after DATA starts are delivery-unknown and are never retried automatically. No Hermes tool, command, hook, callback, timer, or runtime facade can invoke this transport, and runtime `send_enabled` remains false until v0.19 adds confirmation, durable audit, and idempotency. Local drafts remain sensitive plaintext in separate hardened `email-drafts.sqlite3`; the content-free observation ledger remains separate. Windows confidentiality relies on the operator's profile ACL. Never place credential values in settings or commit credentials, tokens, passwords, private keys, real messages, account-specific configuration, draft databases, SMTP captures, or TLS key logs.
