# Security Policy

## Supported versions

The `0.x` series is an early foundation. Security fixes are applied to the latest release.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability or exposed secret. Use GitHub's private security advisory flow for this repository. Include the affected version, impact, reproduction steps, and any suggested mitigation.

## Operational safety

Version `0.11.0` implements only a guarded, one-page-at-a-time facade over deterministic mock retrieval. Page size is capped at 100, and opaque cursors are neither interpreted nor persisted by the plugin. It does not implement production message retrieval or sending. Never commit credentials, tokens, passwords, private keys, real messages, or account-specific configuration. Treat third-party plugins and future provider adapters as trusted code running with the user's permissions.
