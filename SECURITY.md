# Security Policy

## Supported versions

The `0.x` series is an early foundation. Security fixes are applied to the latest release.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability or exposed secret. Use GitHub's private security advisory flow for this repository. Include the affected version, impact, reproduction steps, and any suggested mitigation.

## Operational safety

Version `0.14.0` adds bounded read-only IMAP over verified implicit TLS. Registration, disabled mode, mock mode, and `/email-status` never resolve secrets or connect. Explicit IMAP operations use SASL PLAIN only after verified TLS, require a read-only mailbox, fetch bounded UID ranges with partial `BODY.PEEK`, omit attachments and remote HTML resources, and never follow cursors automatically. SMTP, sending, deletion, movement, polling, retries, and persistence remain unavailable. Never place credential values in plugin settings or commit credentials, tokens, passwords, private keys, real messages, or account-specific configuration. Treat provider adapters as trusted code running with the user's permissions.
