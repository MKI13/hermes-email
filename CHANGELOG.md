# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/MKI13/hermes-email/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/MKI13/hermes-email/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/MKI13/hermes-email/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/MKI13/hermes-email/releases/tag/v0.1.0
