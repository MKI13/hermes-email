# Security Policy

## Supported versions

The `0.x` series is an early foundation. Security fixes are applied to the latest release.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability or exposed secret. Use GitHub's private security advisory flow for this repository. Include the affected version, impact, reproduction steps, and any suggested mitigation.

## Operational safety

Version `0.13.0` adds validated secret references, targeted lazy environment resolution, and redacted process-local secret values without adding a production provider. Registration, disabled mode, and mock mode never resolve secrets. Existing retrieval remains limited to guarded, one-page-at-a-time fetch and local search over deterministic mock data; no component follows `next_cursor`. The plugin does not implement production message retrieval or sending. Never place credential values in plugin settings or commit credentials, tokens, passwords, private keys, real messages, or account-specific configuration. Treat future provider adapters as trusted code running with the user's permissions.
