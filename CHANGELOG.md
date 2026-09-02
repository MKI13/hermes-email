# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/MKI13/hermes-email/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/MKI13/hermes-email/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/MKI13/hermes-email/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/MKI13/hermes-email/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/MKI13/hermes-email/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/MKI13/hermes-email/releases/tag/v0.1.0
