# Security Policy

## Supported versions

The `0.x` series is an early foundation. Security fixes are applied to the latest release.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability or exposed secret. Use GitHub's private security advisory flow for this repository. Include the affected version, impact, reproduction steps, and any suggested mitigation.

## Operational safety

Version `0.17.0` adds opt-in local drafts in a separate private profile-scoped SQLite database. Drafting is disabled by default, requires an explicit account namespace, stores sensitive plaintext only in `email-drafts.sqlite3`, bounds all content and capacity, requires idempotent operation IDs and exact revisions, and supports reversible trash without purge. Draft operations never call a provider, mailbox, network, or secret source; lists omit bodies and recipient details, while gets expose bounded untrusted review windows. The content-free observation ledger remains separate. Both databases verify application/schema identity, integrity, owner-only POSIX paths, and resource caps; Windows relies on the operator's profile ACL. SMTP, sending, provider drafts, mailbox writes, polling, and automatic action remain unavailable. Never place credential values in plugin settings or commit credentials, tokens, passwords, private keys, real messages, account-specific configuration, or draft databases.
