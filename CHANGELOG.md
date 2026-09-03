# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.17.0] - 2026-09-07

### Added

- Opt-in provider-independent local SQLite drafts with explicit account namespaces and separate content storage.
- Six tools for create, body-free list, bounded get, full-replacement update, reversible trash, and restore.
- Durable content-free mutation receipts, caller operation IDs, optimistic revisions, opaque draft IDs, and cancellation-safe outcomes.
- Bounded normalized To, Cc, Bcc, subject, body, reply reference, pagination, draft count, operation count, and database size.

### Changed

- Provider capability and method surfaces are read-only; provider draft and send methods were removed.
- Drafting is disabled by default and configured independently under `drafts` rather than an email-provider mode.
- `/email-status` reports draft readiness and a separate fixed draft diagnostic without opening storage.
- The bundled skill requires a direct current-user request for mutations, exact-revision review, and prompt-injection resistance.
- Project version advanced to `0.17.0` across package, manifest, skill, CI, examples, and documentation.

### Security

- Draft content is isolated in fixed `email-drafts.sqlite3` and never enters the content-free observation ledger.
- Mutation idempotency binds operation kind and normalized request digest; changed reuse and stale revisions fail closed.
- Lists omit body and recipient details, gets expose bounded untrusted windows, and mutation receipts contain no message content.
- Draft operations perform no provider, mailbox, network, DNS, environment, secret, hook, timer, retry, or automatic action.
- Plaintext storage limitations, POSIX permissions, Windows ACL responsibility, backup exposure, and secure-delete limitations are documented.
- SMTP, sending, provider drafts, mailbox writes, purge, polling, automatic replies, and automatic sending remain unavailable.

## [0.16.0] - 2026-09-06

### Added

- Opt-in profile-scoped SQLite observation ledger with exact provider-message deduplication.
- Explicit stable account namespace plus hashed mailbox namespace for cross-account isolation.
- Fixed database application ID, monotonic schema version, integrity verification, retention, row, page, and file-size limits.
- Fixed runtime and tool errors for unavailable, insecure, incompatible, invalid, or full storage.

### Changed

- Explicit list, lookup, and search results are atomically observed before returning when storage is enabled.
- `/email-status` reports whether observation persistence is enabled without opening the database.
- Project version advanced to `0.16.0` across package, manifest, skill, CI, and documentation metadata.

### Security

- Persistence is disabled by default and uses only Hermes' public profile-scoped plugin data directory with a fixed filename.
- The ledger stores no subject, address, body, MIME, RFC Message-ID, host, credential, reference, prompt, tool argument, or arbitrary provider metadata.
- Existing POSIX paths must be private, owner-controlled regular objects; Windows uses the operator-protected Hermes profile directory; corrupt or incompatible databases fail without deletion, recreation, retry, or memory fallback.
- Observation does not mean processed and never suppresses explicit reads or authorize an action.

## [0.15.0] - 2026-09-05

### Added

- Hermes tools for bounded one-page listing, opaque-ID lookup, and one-page local search.
- Model-facing JSON schemas with explicit page, query, identifier, cursor, body-offset, and body-window limits.
- Tool registration metadata in the manifest and bundled skill requirements.

### Changed

- Read-tool handlers return compact JSON text with operation names, caller-driven cursors, and fixed redacted errors.
- List and search expose metadata only; lookup exposes a bounded, continuable body window.
- The email skill now directs Hermes to treat every returned mail field as untrusted data.
- Project version advanced to `0.15.0` across package, manifest, skill, CI, and documentation metadata.

### Security

- Tool availability checks are synchronous and offline; registration performs no provider, network, health, or secret operation.
- Rejected tool names roll back prior registrations and fail loading instead of leaving a foreign or partial toolset.
- Outputs whitelist normalized fields, independently enforce provider page size, cap model-visible strings, omit arbitrary provider metadata, and separate source truncation from body windowing.
- No write tool, tool dispatch, SMTP path, polling, retry, persistence, or automatic action was added.

## [0.14.0] - 2026-09-04

### Added

- Production `ImapReadOnlyProvider` for explicit health, bounded page fetch, and single-message lookup.
- Provider-neutral connection, authentication, TLS, timeout, mailbox, protocol, and message error taxonomy.
- IMAP-specific settings for implicit TLS, credential references, mailbox selection, timeout, mailbox count, per-message bytes, and per-page bytes.
- Redacted runtime health states for configured, ready, authentication failure, and provider reachability.
- Security-focused protocol fakes covering TLS, SASL, read-only selection, UID pagination, partial literals, MIME normalization, lifecycle races, and denial paths.

### Changed

- Provider lifecycle now includes explicit health and cleanup methods.
- Runtime unload closes the provider, prevents late health completion from restoring readiness, and releases the Hermes context.
- Project version advanced to `0.14.0` across package, manifest, skill, CI, and documentation metadata.

### Security

- IMAP permits only verified implicit TLS 1.2 or newer and ignores process-level TLS key-log configuration.
- Authentication uses SASL PLAIN over verified TLS rather than the CPython IMAP LOGIN command path.
- Every mailbox operation requires read-only selection and uses bounded UID `BODY.PEEK` partial fetches; no mutating IMAP commands are implemented.
- UID cursors bind to `UIDVALIDITY`, descend within the current `UIDNEXT` snapshot, and are never followed automatically.
- MIME normalization skips attachments and remote HTML resources, suppresses active or hidden elements, strips control characters, caps content, and marks partial messages.
- Registration, disabled mode, mock mode, `/email-status`, and provider resolution perform no secret or network access.
- SMTP, sends, deletes, moves, polling, retries, tools, and persistence remain unavailable.

## [0.13.0] - 2026-09-03

### Added

- Provider-neutral `SecretResolver` interface and a targeted `EnvironmentSecretResolver` for future provider credential lookup.
- Strict plugin-scoped secret-reference validation and non-sensitive missing-reference errors.
- Process-local `SecretValue` objects with redacted string and representation output.
- Optional `credentials.username_ref` and `credentials.password_ref` configuration fields that store references only.
- Security tests covering validation, targeted lookup, missing values, redaction, no caching, external-operation isolation, and disabled/mock/reload behavior.

### Changed

- Distribution validation now rejects environment files, local configuration, credential exports, private-key files, and unsafe archive members.
- Project version advanced to `0.13.0` across package, manifest, skill, CI, and documentation metadata.

### Security

- Plugin registration, disabled mode, mock mode, and reload do not resolve secrets.
- No production mail provider, account connection, credential value, network operation, persistence, or mail side effect was added.
- Existing runtime safety boundaries remain unchanged.

## [0.12.1] - 2026-09-03

### Added

- Read-only GitHub Actions CI for pushes and pull requests to `main`, with full tests, import checks, and package builds on Python 3.11 through 3.13.
- Distribution-content validation for the generated wheel and source archive.
- A separate clean-home Plugin Doctor job using an immutable Hermes Agent v0.21.0 upstream commit and its supported editable development installation.
- An official GitHub Actions status badge in the README.

### Changed

- Added pinned `pip` bootstrap and `build` frontend requirements for reproducible CI setup.
- Project version advanced to `0.12.1` across package, manifest, skill, and documentation metadata.

### Security

- CI requires no repository secrets and grants only read access to repository contents.
- The official plugin security scan remains a pre-release installation gate because Hermes v0.21.0 exposes no separate non-interactive scan command.
- No runtime or mail functionality changed; all existing safety boundaries remain unchanged.

## [0.12.0] - 2026-09-03

### Added

- Bounded cursor-based pagination for `EmailPlugin.search_messages(query, *, limit=50, cursor=None)` over exactly one provider page.
- Search pagination tests covering query, limit, cursor, provider, one-page, runtime-context, and no-write safety behavior.

### Changed

- Local search now returns `EmailMessagePage`, containing only current-page matches while preserving the provider page's opaque `next_cursor` unchanged.
- Fetch and search now share the same strict limit and cursor validation without clamping, normalization, retries, automatic continuation, or persistence.
- Project version advanced to `0.12.0` across package, manifest, skill, and documentation metadata.

## [0.11.1] - 2026-09-02

### Fixed

- Fixed Hermes Agent v0.21.0 installer compatibility by targeting supported manifest version 1 without changing runtime behavior or safety boundaries.
- Replaced scanner-sensitive synthetic fixtures and ambiguous profile references while preserving redaction, provider rejection, context, and lifecycle coverage.
- Replaced unpinned development install examples with a pinned requirements file.

### Security

- The official Hermes plugin security scan now passes without disabling, overriding, or modifying the scanner.
- No real mail provider, account connection, network operation, send, delete, move, polling, or background job was added.

### Changed

- Project version advanced to `0.11.1` across package, manifest, skill, and documentation metadata.

## [0.11.0] - 2026-09-02

### Added

- Immutable, sequence-compatible `EmailMessagePage` results with provider-owned opaque continuation cursors.
- Deterministic local cursor pagination in `MockEmailProvider`, including explicit rejection of unknown cursors.
- Pagination safety tests for one-page delegation, cursor opacity, fixed limit boundaries, provider failures, runtime-context isolation, and absence of write effects.

### Changed

- `EmailProvider.fetch_messages()` and `EmailPlugin.fetch_messages()` now accept `cursor=None` and return exactly one `EmailMessagePage`.
- Fetch limits are restricted to integers from 1 through `MAX_FETCH_LIMIT = 100` without clamping.
- Project version advanced to `0.11.0` across package, manifest, skill, and documentation metadata.

## [0.10.0] - 2026-09-02

### Added

- Safe `/email-status` diagnostics through Hermes' official in-session `ctx.register_command()` API.
- Fixed text formatting sourced exclusively from the registered `EmailPlugin.get_runtime_status()` snapshot.
- Tests for disabled, mock-ready, and configuration-error output; profile and diagnostic display; redaction; offline execution; mailbox-operation isolation; and lifecycle cleanup.

### Changed

- Project version advanced to `0.10.0` across package, manifest, skill, and documentation metadata.

## [0.9.0] - 2026-09-02

### Added

- Safe runtime settings loading through Hermes' official plugin-scoped `ctx.get_config()` API.
- Immutable health snapshots with `disabled`, `mock-ready`, and `configuration-error` states.
- Fixed non-sensitive diagnostic codes for expected configuration and provider-resolution failures.
- Tests for default disablement, explicit mock readiness, safe error handling, status redaction, offline initialization, and lifecycle cleanup.

### Changed

- `register(ctx)` now initializes `EmailPlugin` from validated runtime settings while preserving skill and context registration.
- Project version advanced to `0.9.0` across package, manifest, skill, and documentation metadata.

## [0.8.0] - 2026-09-02

### Added

- Bounded local `EmailPlugin.search_messages()` facade using deterministic plain substring matching over existing message fields.
- Tests for subject, sender, and body matches; case and whitespace handling; ordering; query rejection; and read-only denial paths.

### Changed

- Project version advanced to `0.8.0` across package, manifest, skill, and documentation metadata.

## [0.7.0] - 2026-09-02

### Added

- Safe read-only `EmailPlugin.get_message()` facade with shared read gates, a dedicated provider get capability, and unchanged opaque message-ID delegation.
- Tests for exact delegation, known and unknown IDs, denial paths, provider failures, and absence of write effects.

### Changed

- Project version advanced to `0.7.0` across package, manifest, skill, and documentation metadata.

## [0.6.0] - 2026-09-02

### Added

- Safe read-only `EmailPlugin.fetch_messages()` facade with independent read-mode, provider-presence, provider-capability, and bounded-limit gates.
- Facade tests covering mock delegation, exact result and limit forwarding, denial paths, and absence of unrelated provider effects.

### Changed

- Project version advanced to `0.6.0` across package, manifest, skill, and documentation metadata.

## [0.5.0] - 2026-09-02

### Added

- Runtime binding from Hermes `register(ctx)` through `ActiveProfileContextSource` to `EmailPlugin`.
- Lifecycle cleanup through the official `ctx.on_unload()` API.
- Tests for public profile propagation, missing and invalid values, empty personality fields, and private-file isolation.

### Changed

- Project version advanced to `0.5.0` across package, manifest, skill, and documentation metadata.

## [0.4.0] - 2026-09-02

### Added

- `EmailPlugin.from_config()` factory backed exclusively by the safe provider resolver.
- Factory tests for resolver delegation, error propagation, safety preservation, and offline operation.

### Changed

- Project version advanced to `0.4.0` across package, manifest, skill, and documentation metadata.

## [0.3.0] - 2026-09-02

### Added

- Explicit provider resolver for `email.provider: mock`.
- Clear errors for missing and unsupported provider identifiers.
- Security tests covering case normalization, suspicious strings, imports, and network access.

### Changed

- Project version advanced to `0.3.0` across package, manifest, skill, and documentation metadata.

## [0.2.0] - 2026-09-02

### Added

- Deterministic `MockEmailProvider` with three synthetic local messages.
- In-memory draft creation with stable mock draft IDs.
- Contract tests for offline fetch, lookup, drafting, capabilities, and send denial.

### Changed

- Project version advanced to `0.2.0` across package, manifest, skill, and documentation metadata.

## [0.1.0] - 2026-09-02

### Added

- Initial project metadata and safe-by-default foundation.

[Unreleased]: https://github.com/MKI13/hermes-email/compare/v0.17.0...HEAD
[0.17.0]: https://github.com/MKI13/hermes-email/compare/v0.16.0...v0.17.0
[0.16.0]: https://github.com/MKI13/hermes-email/compare/v0.15.0...v0.16.0
[0.15.0]: https://github.com/MKI13/hermes-email/compare/v0.14.0...v0.15.0
[0.14.0]: https://github.com/MKI13/hermes-email/compare/v0.13.0...v0.14.0
[0.13.0]: https://github.com/MKI13/hermes-email/compare/v0.12.1...v0.13.0
[0.12.1]: https://github.com/MKI13/hermes-email/compare/v0.12.0...v0.12.1
[0.12.0]: https://github.com/MKI13/hermes-email/compare/v0.11.1...v0.12.0
[0.11.1]: https://github.com/MKI13/hermes-email/compare/v0.11.0...v0.11.1
[0.11.0]: https://github.com/MKI13/hermes-email/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/MKI13/hermes-email/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/MKI13/hermes-email/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/MKI13/hermes-email/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/MKI13/hermes-email/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/MKI13/hermes-email/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/MKI13/hermes-email/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/MKI13/hermes-email/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/MKI13/hermes-email/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/MKI13/hermes-email/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/MKI13/hermes-email/releases/tag/v0.1.0
