# Audit reliability and upgrades

## Version 0.41.0

The optional operational audit helps an operator diagnose mail-tool activity.
It is **not** a tamper-proof compliance log, a send-intent ledger, or evidence
of user approval. Enabling it cannot enable SMTP or authorize an action.

## Configure it for any Hermes profile

The same settings work in an existing single profile or a dedicated mail
profile. Use your actual profile name; no EF-Sinn-specific names are required.

```yaml
hermes:
  profile: email

audit:
  mode: sqlite
  retention_days: 90
  max_events: 10000
  max_database_bytes: 16777216
```

Put these fields under `plugins.entries.hermes-email.settings` in your Hermes
configuration. For a single-profile installation, replace `email` with the
exact active profile, commonly `default`. With audit disabled, no audit
receipt is added to tool results and no audit database is opened.

## Separate operation success from audit success

When auditing is enabled, tool results contain a separate receipt:

```json
{"audit":{"recorded":true,"diagnostic":null,"gap_detected":false}}
```

If audit storage fails, the operation retains its actual result and gains:

```json
{"audit":{"recorded":false,"diagnostic":"audit-write-failed","gap_detected":true}}
```

A returned draft mutation receipt still identifies a committed local draft.
An audit warning does not mean the draft was rolled back. Do not invent a new
operation ID to repeat it. An identical request may be retried with its
original operation ID; the existing draft idempotency checks still apply.
Provider failures likewise retain their original fixed error code.

`/email-status` reports `Audit diagnostic: audit-write-failed` after a failure.
A subsequent successful event has `recorded: true` but retains
`gap_detected: true` for that runtime's lifetime. This does not reconstruct
missing events. A fresh runtime resets this in-memory warning; it cannot
infer gaps left by a previous process. Reported probe success (`ok: true`)
is distinct from provider readiness: a failed health probe is logged with
its diagnostic, not a misleading healthy `ok` outcome.

## Persistence and privacy

The table retains only a sequence number, timestamp, allowlisted operation,
allowlisted outcome, and item count. Subjects, bodies, addresses, identifiers,
search text, attachment filenames, and credentials are not written here.
The database lives in Hermes' profile-scoped plugin data directory.

New POSIX databases are created with mode `0600`, not fixed after the first
write. New directories use `0700`. Unsafe existing file/directory permissions,
symlinks, hard-linked databases, changed database identities, foreign schema
objects and unsafe SQLite sidecars are rejected rather than silently repaired.
SQLite page limits are installed before write transactions. Retention and
record-count pruning are committed together with the new event. The byte
budget limits the main database, not the combined peak size of its rollback
journal and filesystem metadata. Leave disk space for transaction overhead.

Fresh reads return an empty list without creating storage. Reads of existing
files are read-only snapshots. Closed or disabled stores reject access.

## Upgrading legacy audit files

The table layout from v0.30.0 through v0.36.1 is retained. An exact matching
legacy schema with valid rows is adopted during the next write transaction,
using application ID `0x48454155` and schema version `1`. Existing records
remain, subject to the configured retention and record-count limits.
Reading a legacy database does not migrate it.

Stop the affected profile and make a private backup before maintenance.
A legacy file with mode `0644` is rejected. Verify that it is the intended
regular, non-linked audit file in the correct profile directory before an
operator changes that file to `0600` and its dedicated directory to `0700`.
Never recursively change permissions on the user's home or Hermes tree.
Unknown schema versions, foreign objects and corrupt files are not reset
or deleted automatically. Restore a verified backup or investigate them.

## Scope and verification

POSIX permissions and path checks do not isolate mutually hostile processes
running under the same operating-system user. Profiles are an operational
boundary, not a substitute for separate OS accounts or containers. On
Windows, protect the directory with suitable user-only ACLs. The audit store
does not install or verify Windows ACLs.

Run the regression suite with the project's test dependencies installed:

```sh
python -m pytest tests/test_audit.py tests/test_audit_security.py tests/test_audit_integration.py
```

The tests cover every registered mail tool with audit enabled, invalid
arguments, storage failures after draft commits, idempotent retry, original
provider errors, health diagnostics, permissions, links, schema validation,
legacy adoption, resource limits, retention and concurrent store instances.
The Hermes-specific registry test additionally requires the pinned Hermes
integration environment used by CI.

SQLite reference: [URI open modes](https://www.sqlite.org/uri.html) and
[database page limits](https://www.sqlite.org/pragma.html#pragma_max_page_count).
