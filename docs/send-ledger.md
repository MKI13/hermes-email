# Send-ledger safety

## Current implementation: 0.39.0

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
4. Known v1/v2/v3 schemas are adopted transactionally into schema 4 with application
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

### Cross-process ownership (roadmap step 2)

Each data directory has a persistent `.dispatch` file, bound into version 2 of
its identity anchor. The dispatch lease is acquired before persisting a new
intent and held until the synchronous SMTP call and terminal-state handling
have ended. Short database transactions use a different `.guard` lock: observers
can read a live `dispatching` record without waiting for the SMTP call.

Recovery first tries the dispatch lease without blocking. If held, it makes no
recovery changes. If acquired, no compliant current-version worker owns a live
attempt: remaining `dispatching` records become `delivery-unknown`, never retryable.
No PID, process token, heartbeat expiry or elapsed-time heuristic grants recovery.
SIGSTOP does not release the kernel lease; a crashed process does. A fork child
closes inherited descriptors without unlocking the parent's open-file description;
inherited store ownership is discarded before reuse. Closing a store while it
owns an attempt raises a busy error instead of releasing another worker's lease.
An abandoned lease object closes its descriptor on finalization; an active
synchronous sender retains a strong reference until its completion/finally path.
Abandonment never removes the durable intent or permits redispatch.

For conservative operation, one profile/data directory dispatches one message at
a time. A concurrent identical operation gets a replay/status receipt. Another
new operation receives `SendBusyError` without creating an intent or reaching
SMTP. The same draft/revision under another ID is rejected. Different data
directories are independent. Do not run a long-lived SMTP worker in the same
process as an untrusted plugin with unrestricted filesystem or Python access.

The `.dispatch` inode is anchored persistently, so replacement/deletion is a
storage error in both existing and fresh store instances. Do not clean up lock
files. Kernel/advisory lock guarantees require cooperating current-version
workers and a supported local filesystem. NFS/SMB is not validated. POSIX locking
is verified on Linux; Windows uses `msvcrt` locking but native Windows/ACL behavior
is not covered by the Linux CI and must be separately validated. Missing lock
support fails closed; there is no process-local fallback.

### Legacy open attempts and upgrades

Schema 1/2/3 may contain `dispatching` rows without this lease protocol. Automatic
migration of those rows is refused by default: an old worker might still be alive.
After stopping **all** older workers and retaining a private backup, a trusted
operator may perform a one-time migration using:

```python
ledger = SqliteSendIntentStore(profile_data_dir, legacy_workers_stopped=True)
ledger.recover_interrupted_dispatches()
ledger.close()
```

This library-only flag is an operator attestation, not a model-generated approval
and not send authority. It is unnecessary for quiescent legacy terminal records.
The migration retains prior rows and UUIDs; unknown legacy Date/Message-ID values
remain null rather than being invented. Never run old and new send workers against
the same ledger. An interrupted anchor/schema upgrade fails closed for inspection.

### Stable message preparation and replay

Trusted orchestration can call `prepare_send_candidate` with the existing exact
confirmation/draft revision and `send_operation_id`, omitting both `message_id`
and `message_date`. Message-ID is deterministically derived from the operation,
account, sender, draft/revision and confirmation. Date comes from the persisted
draft update time, not a fresh wall-clock reading. Actual attempt times remain
separate ledger timestamps. Existing callers can still supply an explicit ID/date
pair; they must retain it, and partial pairs are refused.

The intent stores the exact Message-ID and Date plus the digest binding all
submission fields and bytes. A replay with different body, recipients, date,
Message-ID, confirmation or revision fails closed. The ledger never substitutes
new content into an earlier attempt. It does not retain message body bytes. A
transport return that is not an explicit accepted result is treated as uncertain.
If SMTP returns but terminal persistence fails, the durable prior intent continues
to block retries and is recovered to uncertainty once storage is available.

### Process evidence

Tests launch independent `spawn` processes simultaneously, observe the live owner,
kill and pause workers, exercise fork inheritance and foreign-thread finish/close,
and replace lock files. One test uses a real loopback-only synthetic SMTP server:
two processes produce exactly one DATA transaction with unchanged message bytes.
Other failure tests use injected transports and never connect to a real account.
These deterministic tests exercise the component directly inside the isolated
profile; an LLM turn would add no evidence about file-lock or crash correctness.
