# Send-ledger safety

## Current implementation: 0.38.0

The send ledger is mandatory infrastructure for at-most-once SMTP *attempts*.
It is not the optional operational audit, and cannot prove end-to-end delivery.
No model-facing sending is enabled by this release.

### Private, durable, non-pruning storage

`email-send-intents.sqlite3` is created privately before SQLite opens it. Existing
paths must already be safe. Nothing silently changes their permissions. On POSIX,
the immediate data directory needs owner-only rwx and files need owner-only rw.
Ancestor symlinks, non-regular files, hardlinks, unsafe sidecars and changed database
identities are rejected. Windows deployments additionally need operator-managed
private ACLs; POSIX mode tests are not Windows isolation guarantees.

A durable `.identity` file binds database device/inode and a random ledger UUID.
A `.guard` file serializes short database transactions across cooperating processes.
Neither file is temporary: NEVER unlink it during operation. A missing or replaced
initialized database must not be automatically recreated. The identity anchor
also detects replacement in a new store instance. It is not protection against
malicious code that can rewrite the entire directory as the same OS user.

SQLite uses DELETE journals, synchronous=EXTRA, a pre-write page limit and full
schema/row/integrity validation. The default database cap is 64 MiB and 100,000
intents. Trusted library callers may set `max_database_bytes` (1–256 MiB) and
`max_intents` (1–1,000,000). There is NO automatic deletion or expiry of send
records. Capacity exhaustion fails closed; operator maintenance must preserve
all prior attempts and their uniqueness constraints. Journal files have their
own bounded overhead. These limits are not a hard operating-system disk quota.

### Upgrade and recovery

1. Stop all older send workers before upgrading. Mixed-version workers are not
   supported, particularly while migrating an old dispatching record.
2. Back up the entire profile data directory privately while it is quiescent.
3. Validate ownership and permissions. Do not discard a file to make an error go away.
4. Known v1/v2 schemas are adopted transactionally into schema 3 with application
   identity. Unknown schemas, extra triggers, malformed rows and invalid versions
   are rejected without deleting evidence. All prior states are retained except
   orphan recovery may conservatively mark dispatching as delivery-unknown.
5. An interrupted first initialization/adoption may leave a protective identity
   anchor. Fail closed and investigate; do not automatically erase it.

Moving or restoring the database changes its identity. Such recovery requires a
separate, explicit operator-reviewed workflow, not deletion of the anchor in a
running installation. Restoring an older backup alone can lose later send
attempts and must NEVER be treated as permission to redispatch. Complete deletion
of the ledger and anchor cannot be detected without an independent backup/trust
anchor. No claim of protection against hostile same-user processes is made.

### Verification

Tests cover first-write mode under umask 000, read-only files/directories,
corruption, truncation, deletion, replacement across store instances and between
begin/transport, unsafe sidecars, malformed/foreign schemas, all legacy states,
transaction rollback, capacity preservation, actual SQLite page exhaustion,
closed stores and failed fsync. Every denied dispatch test observes zero fake
transport submissions. No real mail or external model is required.

Process ownership/recovery is addressed separately by roadmap step 2. The old
process-token heuristic must not be considered a complete live-owner proof.
