# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.29.0] - 2026-09-05

### Added
- Deterministic metadata-only attachment handling classes: `document`, `image`, `archive`, `active-content`, and `unknown`.
- `potentially_active` plus explicit deny fields for automatic opening, execution, and content access.

### Security
- Handling classes are warnings derived from untrusted metadata, not trust labels. Attachment bytes remain unavailable and no class grants action authority.


## [0.28.0] - 2026-09-05

### Added

- Provider-neutral `EmailAttachment` metadata with message-local attachment ID, bounded filename, MIME type, optional decoded size, disposition, and filename-truncation state.
- Read/thread detail outputs now expose at most 25 attachment metadata records; list/search summaries remain content-minimized and omit attachments.
- IMAP MIME normalization identifies attachment parts without exposing their content, including nested `message/rfc822` attachments as one outer attachment.

### Security

- Attachment tool output is metadata-only with fixed `metadata_is_untrusted: true`, `content_available: false`, and `authorization: none`.
- No attachment bytes, local paths, URLs, download/open/render/execute actions, or automatic file handling are introduced.
- Tool-boundary validation re-bounds attachment IDs, filenames, MIME values, sizes, dispositions, and total attachment count independently of provider behavior.

## [0.27.0] - 2026-09-05

### Added

- Deterministic operator-configured sender classification for `internal`, `customer`, `supplier`, and `unknown-external`.
- Exact-address rules take precedence over domain rules; conflicting exact address/domain category rules fail configuration validation.
- `sender_classification` appears in list, search, message-detail, and thread outputs with fixed `authorization: none`.

### Security

- Sender category is never inferred from message text, display name, model judgment, or claimed role.
- Classification never authorizes tools, drafting, sending, secret access, profile changes, recipient decisions, or policy changes.

## [0.26.0] - 2026-09-05

### Fixed

- Community-source installation no longer trips Hermes plugin-guard on the repository's own prompt-injection regression fixture.
- The malicious-mail regression payload is still identical at runtime but is assembled from inert string fragments so static install scanning does not mistake test data for executable agent instructions.

### Changed

- Version advanced to `0.26.0` across package metadata, manifest, skill, CI assertions, imports, README, and current documentation.
- Hermes Plugin Doctor CI now also executes an install-guard regression against the complete repository tree.

### Security

- A release is blocked when the pinned Hermes `plugin_guard` reports any high/critical finding or a non-safe verdict, closing the gap between unit/Doctor success and real `hermes plugins install` behavior.
- Existing prompt-injection protections remain unchanged: malicious email text is untrusted data with zero action authority.

## [0.25.0] - 2026-09-05

### Added

- Provider-neutral safe Reply-To routing with explicit `from` versus `reply-to` source, bounded candidates, validity, truncation, ambiguity, and optional selected address.
- Model-facing `reply_route` metadata in existing message/thread detail results with fixed `authorization: none`.
- Tests for absent, single, multiple, malformed, and oversized Reply-To headers plus real IMAP normalization.

### Changed

- IMAP now normalizes `Reply-To` separately from `From`. A single valid Reply-To takes precedence; From is used only when Reply-To is absent.
- Multiple, invalid, or more than ten Reply-To candidates never receive an automatic selection.
- Version advanced to `0.25.0` across package metadata, manifest, skill, CI, tests, README, and current documentation.

### Security

- Reply-To remains untrusted external metadata and cannot authorize draft creation, tool use, recipient mutation, confirmation, SMTP dispatch, or retry.
- A present but invalid Reply-To does not silently fall back to From, preventing malformed-header routing from becoming an implicit recipient decision.

## [0.24.0] - 2026-09-05

### Added

- Bounded provider-neutral RFC thread reconstruction using `Message-ID`, `In-Reply-To`, and `References` only.
- New read-only Hermes tool `email_get_thread` with explicit scan completeness, result truncation, unresolved-reference count, and bounded per-message body windows.
- IMAP normalization of bounded reply/reference headers for real mailbox thread context.
- Thread resolver and tool tests covering subject-collision resistance, sibling replies, missing RFC headers, truncation, and unresolved references.

### Changed

- The read toolset expands from three to four tools and the manifest now advertises ten total Hermes tools.
- Current package, manifest, skill, CI, README, architecture, configuration, security, compatibility, imports, and version-consistency checks advance to `0.24.0`.

### Security

- Thread membership never uses subject, sender, body, semantic similarity, or UID proximity, preventing ordinary lookalike messages from being merged into one business conversation.
- Thread results remain untrusted external content and cannot authorize drafting, sending, tools, secrets, profile changes, or any other side effect.
- Thread scanning is bounded and never automatically paginates; incomplete context is surfaced rather than silently claimed complete.

## [0.23.0] - 2026-09-05

### Added

- Prompt-injection/content-trust contract tests that require model-facing mail output to remain explicitly untrusted and require the bundled skill to retain core no-authority rules.
- Universal installation/profile guide covering both a recommended dedicated email profile and a fully supported existing single-profile setup.
- Separate productive configuration examples for users with a dedicated mail profile and users who bind Hermes Email to an existing profile.

### Changed

- The email skill now explicitly assigns zero action authority to sender names, subjects, bodies, signatures, quoted/forwarded text, HTML-derived text, headers, attachment metadata, fake system/developer text, tool-like syntax, and claimed authority.
- README/configuration/architecture/security/compatibility documentation now makes clear that a dedicated mail profile is recommended but not required; the invariant is one explicit productive mail-owning profile.
- CI now runs the prompt-injection contract alongside profile isolation and existing read/draft/SMTP/idempotency/recovery tests.
- Project version advanced to `0.23.0` across package metadata, plugin manifest, skill, CI assertions, imports, README, architecture, configuration, compatibility, security documentation, and changelog.

### Security

- Reading external mail authorizes only the current user's requested read operation; mail content cannot independently authorize another tool call or external side effect.
- Mail/draft content cannot authorize secret access, profile changes, recipient changes, draft mutation, confirmation, SMTP dispatch, policy changes, or retry of `delivery-unknown`.
- Prompt-injection hardening preserves original customer content as evidence/data rather than destructively rewriting suspicious phrases; the security control is zero action authority.
- Existing profile isolation, exact current-user confirmation, durable duplicate suppression, and strict `delivery-unknown` behavior remain unchanged.

## [0.22.0] - 2026-09-05

### Added

- Official-entrypoint `profile_guard` that binds production mail capabilities to one explicit Hermes profile before provider, state-directory, database, secret, tool, or skill access.
- `profile-blocked` diagnostic runtime that registers only `/email-status` and unload cleanup on denied profiles.
- Production safety rule requiring explicit `hermes.profile` ownership for real IMAP, persistent observation storage, persistent drafts, SMTP submission, or send authorization.
- Development-only `hermes.profile: auto` compatibility for mock/non-persistent configurations.
- Profile-isolation tests proving blocked profiles do not touch `ctx.state`, register mail tools, or register the email skill.

### Changed

- Root directory-plugin registration now routes through the profile guard instead of directly entering the core email runtime.
- Authorized status output reports the current package version and profile-isolation state dynamically; the stale hard-coded `Send: unavailable in v0.18` status path is no longer used by the official entrypoint.
- README, skill, architecture, configuration, security, compatibility, CI, distribution checks, imports, package metadata, and manifest are synchronized to version `0.22.0`.

### Security

- Accidentally loading a production mailbox configuration in another Hermes profile now fails closed before provider resolution, SQLite access, or secret resolution.
- A blocked profile exposes no email read tools, draft tools, email skill, SMTP eligibility, or send path.
- Profile ownership does not imply send authorization; exact user confirmation, durable idempotency, and `delivery-unknown` rules remain unchanged.

## [0.21.0] - 2026-09-05

### Added

- Strict `delivery-unknown` recovery semantics with explicit manual-review and automatic-retry-forbidden status on `SendAttemptRecord`.
- Process-scoped dispatcher ownership for live `dispatching` rows so concurrent same-process callers do not misclassify an active SMTP attempt as crashed.
- Safe v1-to-v2 send-intent schema migration adding nullable dispatcher ownership without recreating or discarding existing records.
- Recovery tests for legacy v0.20 `dispatching`, same-process concurrency, unexpected exceptions, terminal unknown replay, and migration safety.

### Changed

- Unexpected exceptions during a live SMTP attempt are persisted as `delivery-unknown` before propagation whenever the process remains alive.
- Any prior-process, legacy, or unowned unresolved `dispatching` record is atomically converted to `delivery-unknown` on recovery and is never redispatched.
- Process-local locking now serializes orchestration against the same profile ledger while SQLite uniqueness remains the durable duplicate barrier.
- `delivery-unknown` is documented as “message may already have been accepted”; callers must perform manual external verification instead of automatic retry.
- Project version advanced to `0.21.0` across package metadata, plugin manifest, skill, CI assertions, tests, README, architecture, configuration, compatibility, security documentation, and changelog.

### Security

- Restart recovery can no longer leave an old `dispatching` record indefinitely ambiguous or accidentally create a resend path.
- A live same-process dispatch is distinguished from a stale prior-process dispatch before recovery.
- `delivery-unknown` and recovered interrupted dispatch are terminal for automatic behavior; no recovery path calls SMTP.
- SMTP dispatch remains unreachable from `EmailPlugin`, all nine Hermes tools, `/email-status`, the skill, hooks, callbacks, timers, and pollers. No model-facing send tool is exposed.

## [0.20.0] - 2026-09-05

### Added

- Durable profile-scoped `SqliteSendIntentStore` using fixed `email-send-intents.sqlite3` storage for send operation identity, exact draft revision, confirmation identity, request digest, fixed state, and timestamps.
- `IdempotentSendOrchestrator` that commits a `dispatching` intent before the first SMTP call and never redispatches a persisted operation.
- Opaque 16-to-128-character `send_operation_id` validation plus exact SHA-256 candidate binding.
- Durable states for `dispatching`, `accepted`, `definite-failure`, and `delivery-unknown`.
- Restart, crash, replay, delivery-unknown, changed-operation, duplicate-draft, and private-permission test coverage.

### Changed

- The same `send_operation_id` with the same exact candidate now replays durable state without another SMTP attempt.
- The same `send_operation_id` with changed candidate content fails closed.
- A unique `(draft_id, revision)` constraint prevents the same reviewed draft revision from being dispatched under a second operation ID or second confirmation token.
- Unexpected process failure after intent persistence intentionally leaves `dispatching`; restart replays that unresolved state without transport activity.
- Project version advanced to `0.20.0` across package metadata, plugin manifest, skill, CI assertions, tests, README, architecture, configuration, compatibility, security documentation, distribution checks, and changelog.

### Security

- Send intent is durable before SMTP dispatch, closing the restart/retry window that could otherwise duplicate a customer email.
- `delivery-unknown` and unresolved `dispatching` records are never automatically retried.
- The send-intent ledger stores no subject, message body, recipient address, SMTP credential, or raw MIME.
- SMTP dispatch remains unreachable from `EmailPlugin`, all nine Hermes tools, `/email-status`, the skill, hooks, callbacks, timers, and pollers. No model-facing send tool is exposed.

## [0.19.0] - 2026-09-05

### Added

- Trusted-runtime `UserSendConfirmation` proof bound to one exact local draft ID, exact revision, and bounded opaque confirmation ID.
- Dedicated `SendGateConfirmationError` for missing or mismatched current-user confirmation.
- Confirmation tests covering absent authorization, wrong draft identity, wrong revision, stale revisions, and successful exact binding.

### Changed

- `prepare_send_candidate()` now requires explicit current-user confirmation before draft access when technical sending is otherwise armed.
- Immutable send candidates carry the confirmation ID for future durable audit and send-intent binding.
- Project version advanced to `0.19.0` across package metadata, plugin manifest, skill, CI assertions, tests, README, architecture, configuration, compatibility, security documentation, and changelog.

### Security

- Model output, email content, draft content, configuration, recipient policy, SMTP readiness, and `safety.allow_send` cannot substitute for explicit current-user confirmation.
- Confirmation must match the exact draft ID and revision; any draft revision change invalidates prior confirmation automatically.
- SMTP dispatch remains unreachable from `EmailPlugin`, all nine Hermes tools, `/email-status`, the skill, hooks, callbacks, timers, and pollers. Durable audit, idempotent send intent, and delivery-unknown recovery remain required before a Hermes send surface is exposed.

## [0.18.0] - 2026-09-08

### Added

- Strict SMTP submission configuration, separate lazy credential references, fixed sender and account binding, and deployment-owned deny/allowlist/allow-all recipient policy.
- Deterministic bounded plain-text MIME candidate preparation for one exact active local draft revision.
- Single-attempt production SMTP transport with verified implicit TLS or mandatory STARTTLS, AUTH PLAIN after TLS, all-recipient RCPT gating, and explicit accepted, rejected, definite pre-DATA, and delivery-unknown outcomes.
- Protocol fakes covering TLS, authentication, command order, recipient rejection, DATA ambiguity, no retry, keylog suppression, redaction, close, and lifecycle behavior.

### Changed

- Shared ASCII address and display-name normalization now serves draft storage, recipient policy, MIME preparation, and SMTP envelopes.
- `/email-status` reports non-secret SMTP configuration and armed technical-gate state while `send_enabled` remains false.
- Project version advanced to `0.18.0` across package, manifest, skill, CI, examples, and documentation.

### Security

- SMTP defaults disabled and recipient authorization defaults deny. Secrets resolve only after verified TLS and AUTH PLAIN capability checks; protocol debug and TLS key logging remain disabled.
- The fixed configured sender, matching draft/SMTP namespace, exact draft revision, all To/Cc/Bcc recipients, final MIME size, framing, line length, and absence of a Bcc header fail closed.
- Any RCPT rejection prevents DATA. A transport exception after DATA starts becomes delivery-unknown and receives no automatic retry; final server acceptance does not claim delivery.
- SMTP dispatch remains unreachable from `EmailPlugin`, all nine Hermes tools, `/email-status`, the skill, hooks, callbacks, timers, and pollers. Status exposes only fixed non-secret configuration booleans. v0.19.0 must add confirmation, durable audit, and send idempotency before exposing dispatch.

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

- IMAP permits only verified implicit TLS 1.2 or newer and ignores `SSLKEYLOGFILE`.
- Authentication uses SASL PLAIN over verified TLS rather than the CPython IMAP LOGIN command path.
- Every mailbox operation requires read-only `EXAMINE` and uses bounded UID `BODY.PEEK` partial fetches; no mutating IMAP commands are implemented.
- UID cursors bind to `UIDVALIDITY`, descend from a fixed `UIDNEXT` snapshot, and are never followed automatically.
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
- Tests for subject, sender, and body matches; case and whitespace handling, ordering, query rejection, and read-only denial paths.

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

[Unreleased]: https://github.com/MKI13/hermes-email/compare/v0.25.0...HEAD
[0.25.0]: https://github.com/MKI13/hermes-email/compare/v0.24.0...v0.25.0
[0.24.0]: https://github.com/MKI13/hermes-email/compare/v0.23.0...v0.24.0
[0.23.0]: https://github.com/MKI13/hermes-email/compare/v0.22.0...v0.23.0
[0.22.0]: https://github.com/MKI13/hermes-email/compare/v0.21.0...v0.22.0
[0.21.0]: https://github.com/MKI13/hermes-email/compare/v0.20.0...v0.21.0
[0.20.0]: https://github.com/MKI13/hermes-email/compare/v0.19.0...v0.20.0
[0.19.0]: https://github.com/MKI13/hermes-email/compare/v0.18.0...v0.19.0
[0.18.0]: https://github.com/MKI13/hermes-email/compare/v0.17.0...v0.18.0
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
