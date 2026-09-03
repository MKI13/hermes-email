# Security Policy

## Supported versions

The `0.x` series is an early foundation. Security fixes are applied to the latest release.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability or exposed secret. Use GitHub's private security advisory flow for this repository. Include the affected version, impact, reproduction steps, and any suggested mitigation.

## Operational safety

Version `0.15.0` exposes bounded list, lookup, and local one-page search through Hermes' public tool API. Tool registration and availability checks perform no secret lookup, connection, or health operation. Outputs whitelist normalized fields, omit bodies from list and search, window lookup bodies, label mail as untrusted content, and map failures to fixed codes without exception details. Explicit IMAP operations retain verified implicit TLS, SASL PLAIN, read-only mailbox enforcement, bounded UID `BODY.PEEK`, attachment omission, and caller-driven cursors. SMTP, sending, deletion, movement, polling, retries, and persistence remain unavailable. Never place credential values in plugin settings or commit credentials, tokens, passwords, private keys, real messages, or account-specific configuration. Treat provider adapters as trusted code running with the user's permissions.
