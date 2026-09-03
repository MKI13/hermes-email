# Security Policy

## Supported versions

The `0.x` series is an early foundation. Security fixes are applied to the latest release.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability or exposed secret. Use GitHub's private security advisory flow for this repository. Include the affected version, impact, reproduction steps, and any suggested mitigation.

## Operational safety

Version `0.16.0` adds opt-in exact-identity observation deduplication in a private profile-scoped SQLite database. Persistence is disabled by default, opens only after an explicit read, stores no message content or credentials, verifies application/schema identity and integrity, enforces owner-only POSIX paths and resource caps (Windows relies on the operator's profile-directory ACL), and fails closed without recreation, retry, or memory fallback. An observation never means processed or suppresses explicit reads. Read tools retain bounded whitelisted output, untrusted-content markers, fixed errors, and caller-driven cursors; IMAP retains verified TLS, SASL PLAIN, read-only selection, and bounded `BODY.PEEK`. SMTP, sending, stored drafts, deletion, movement, polling, and automatic action remain unavailable. Never place credential values in plugin settings or commit credentials, tokens, passwords, private keys, real messages, or account-specific configuration.
