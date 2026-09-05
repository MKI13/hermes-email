"""Bounded, private operational audit; never a send authorization ledger.

Audit failures are surfaced separately from the already completed mail action.
This file retains the legacy table layout and adopts it only after validation.
"""

from __future__ import annotations

import os
import sqlite3
import stat
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from .config import AuditSettings

_ALLOWED_OPERATIONS = frozenset({
    "list", "get", "search", "thread", "health", "draft-create",
    "draft-reply-create", "draft-reply-all-create", "draft-list", "draft-get",
    "draft-update", "draft-trash", "draft-restore", "draft-send-review",
})
_ALLOWED_OUTCOMES = frozenset({
    "ok", "reading-disabled", "provider-unavailable", "operation-unsupported",
    "invalid-query", "invalid-message-id", "invalid-cursor", "invalid-arguments",
    "storage-insecure", "storage-incompatible", "storage-full", "storage-invalid",
    "storage-unavailable", "authentication-failed", "tls-failed", "provider-timeout",
    "provider-unreachable", "mailbox-unavailable", "protocol-error", "message-error",
    "provider-error", "internal-error", "reply-source-unavailable",
    "reply-route-unavailable", "drafting-disabled", "draft-not-found", "draft-conflict",
    "draft-state-conflict", "draft-limit-reached", "draft-storage-insecure",
    "draft-storage-incompatible", "draft-storage-busy", "draft-storage-full",
    "draft-runtime-closed", "draft-storage-unavailable", "draft-error", "storage-error",
})
_TABLE_SQL = (
    "CREATE TABLE audit_events(id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "created_at INTEGER NOT NULL,operation TEXT NOT NULL,"
    "outcome TEXT NOT NULL,item_count INTEGER NOT NULL)"
)
_INDEX_SQL = "CREATE INDEX audit_created ON audit_events(created_at)"
_SCHEMA = {
    ("table", "audit_events"): _TABLE_SQL,
    ("index", "audit_created"): _INDEX_SQL,
    ("table", "sqlite_sequence"): "CREATE TABLE sqlite_sequence(name,seq)",
}
_APPLICATION_ID = 0x48454155
_SCHEMA_VERSION = 1
_TIMEOUT_SECONDS = 5


class AuditError(RuntimeError):
    """A fixed, content-free audit failure safe for operator diagnostics."""


class ContentMinimizedAuditStore:
    """Serialize bounded transactions without silently repairing unsafe paths.

    POSIX mode checks are not an OS sandbox against a hostile process running
    as the same user. Windows deployments must secure the directory with ACLs.
    """

    def __init__(
        self, path: str | Path, settings: AuditSettings, *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = Path(os.path.abspath(path))
        self.settings = settings
        self._clock = clock
        self._lock = threading.RLock()
        self._closed = False
        self._identity: tuple[int, int] | None = None

    def close(self) -> None:
        """Wait for active operations and reject all subsequent reads/writes."""
        with self._lock:
            self._closed = True

    def record(self, operation: str, outcome: str, item_count: int = 0) -> None:
        """Commit one allowlisted event, pruning within the same transaction."""
        _validate_event(operation, outcome, item_count)
        with self._lock:
            self._ensure_open()
            with self._connection(create=True) as connection:
                assert connection is not None
                connection.execute("BEGIN IMMEDIATE")
                self._verify_schema(connection, initialize=True)
                now = int(self._clock())
                if not 0 <= now <= 9_223_372_036_854_775_807:
                    raise AuditError("invalid audit timestamp")
                cutoff = now - self.settings.retention_days * 86_400
                connection.execute(
                    "DELETE FROM audit_events WHERE created_at < ?", (cutoff,)
                )
                # Prune BEFORE inserting so a full ledger can reuse freed pages.
                connection.execute(
                    "DELETE FROM audit_events WHERE id NOT IN "
                    "(SELECT id FROM audit_events ORDER BY id DESC LIMIT ?)",
                    (self.settings.max_events - 1,),
                )
                connection.execute(
                    "INSERT INTO audit_events(created_at,operation,outcome,item_count) "
                    "VALUES(?,?,?,?)", (now, operation, outcome, item_count),
                )
                connection.commit()

    def recent(self, limit: int = 25) -> list[dict[str, int | str]]:
        """Read a snapshot; an unused store returns [] without creating files."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise AuditError("invalid audit limit")
        with self._lock:
            self._ensure_open()
            with self._connection(create=False) as connection:
                if connection is None:
                    return []
                connection.execute("BEGIN")
                if not self._verify_schema(connection, initialize=False):
                    return []
                rows = connection.execute(
                    "SELECT created_at,operation,outcome,item_count FROM audit_events "
                    "WHERE created_at >= ? ORDER BY id DESC LIMIT ?",
                    (int(self._clock()) - self.settings.retention_days * 86_400, min(limit, self.settings.max_events)),
                ).fetchall()
                result = []
                for timestamp, operation, outcome, count in rows:
                    _validate_event(operation, outcome, count)
                    if not isinstance(timestamp, int) or timestamp < 0:
                        raise AuditError("invalid stored audit timestamp")
                    result.append({"created_at": timestamp, "operation": operation,
                                   "outcome": outcome, "item_count": count})
                return result

    def _ensure_open(self) -> None:
        if self._closed:
            raise AuditError("audit store is closed")
        if self.settings.mode != "sqlite":
            raise AuditError("audit store is disabled")

    @contextmanager
    def _connection(self, *, create: bool) -> Iterator[sqlite3.Connection | None]:
        connection = None
        try:
            identity = self._prepare_path(create=create)
            if identity is None:
                yield None
                return
            # rw deliberately cannot recreate a file removed after path validation.
            mode = "rw" if create else "ro"
            connection = sqlite3.connect(
                self.path.as_uri() + "?mode=" + mode, uri=True,
                timeout=_TIMEOUT_SECONDS, isolation_level=None,
            )
            self._verify_identity(identity)
            connection.execute("PRAGMA trusted_schema=OFF")
            connection.execute("PRAGMA temp_store=MEMORY")
            connection.execute("PRAGMA secure_delete=ON")
            self._enforce_page_limit(connection, writable=create)
            if create:
                if connection.execute("PRAGMA journal_mode").fetchone() != ("delete",):
                    raise AuditError("audit journal mode is unavailable")
                connection.execute("PRAGMA synchronous=FULL")
            else:
                connection.execute("PRAGMA query_only=ON")
            yield connection
        except (OSError, sqlite3.Error, ValueError, OverflowError):
            raise AuditError("audit storage operation failed") from None
        finally:
            if connection is not None:
                try:
                    if connection.in_transaction:
                        connection.rollback()
                finally:
                    connection.close()

    def _prepare_path(self, *, create: bool) -> tuple[int, int] | None:
        parents = list(reversed(self.path.parents))
        for parent in parents:
            try:
                status = parent.lstat()
            except FileNotFoundError:
                if not create:
                    return None
                try:
                    parent.mkdir(mode=0o700)
                except FileExistsError:
                    pass
                status = parent.lstat()
            if not stat.S_ISDIR(status.st_mode):
                raise AuditError("audit directory is unsafe")
            if parent == self.path.parent:
                _verify_private(status)
        try:
            status = self.path.lstat()
        except FileNotFoundError:
            if not create:
                return None
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            try:
                descriptor = os.open(self.path, flags, 0o600)
            except FileExistsError:
                pass
            else:
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            status = self.path.lstat()
        self._verify_file(status)
        identity = (status.st_dev, status.st_ino)
        if self._identity is not None and self._identity != identity:
            raise AuditError("audit database identity changed")
        self._identity = identity
        # Refuse hostile SQLite sidecar paths instead of changing their permissions.
        for suffix in ("-journal", "-wal", "-shm"):
            try:
                self._verify_file(Path(str(self.path) + suffix).lstat())
            except FileNotFoundError:
                pass
        return identity

    def _verify_file(self, status: os.stat_result) -> None:
        if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1:
            raise AuditError("audit file is unsafe")
        _verify_private(status)
        if status.st_size > self.settings.max_database_bytes:
            raise AuditError("audit database exceeds size limit")

    def _verify_identity(self, identity: tuple[int, int]) -> None:
        status = self.path.lstat()
        self._verify_file(status)
        _verify_private(self.path.parent.lstat())
        if (status.st_dev, status.st_ino) != identity:
            raise AuditError("audit database identity changed")

    def _enforce_page_limit(self, connection: sqlite3.Connection, *, writable: bool) -> None:
        size = connection.execute("PRAGMA page_size").fetchone()[0]
        count = connection.execute("PRAGMA page_count").fetchone()[0]
        maximum = self.settings.max_database_bytes // size
        if maximum < 1 or count > maximum:
            raise AuditError("audit database exceeds size limit")
        if writable:
            if connection.execute(f"PRAGMA max_page_count={maximum}").fetchone() != (maximum,):
                raise AuditError("audit database size limit is unavailable")

    def _verify_schema(self, connection: sqlite3.Connection, *, initialize: bool) -> bool:
        identity = (
            connection.execute("PRAGMA application_id").fetchone()[0],
            connection.execute("PRAGMA user_version").fetchone()[0],
        )
        if identity not in {(0, 0), (_APPLICATION_ID, _SCHEMA_VERSION)}:
            raise AuditError("audit database identity is incompatible")
        objects = {(kind, name): sql for kind, name, sql in connection.execute(
            "SELECT type,name,sql FROM sqlite_master"
        )}
        if not objects and identity == (0, 0):
            if not initialize:
                return False
            connection.execute(_TABLE_SQL)
            connection.execute(_INDEX_SQL)
        elif objects != _SCHEMA:
            raise AuditError("audit database schema is incompatible")
        if connection.execute("PRAGMA quick_check(1)").fetchone() != ("ok",):
            raise AuditError("audit database integrity failed")
        if identity == (0, 0) and initialize:
            # Legacy adoption is transactional and never drops existing rows.
            for timestamp, operation, outcome, count in connection.execute(
                "SELECT created_at,operation,outcome,item_count FROM audit_events"
            ):
                _validate_event(operation, outcome, count)
                if not isinstance(timestamp, int) or timestamp < 0:
                    raise AuditError("invalid stored audit timestamp")
            connection.execute(f"PRAGMA application_id={_APPLICATION_ID}")
            connection.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
        return True


def _verify_private(status: os.stat_result) -> None:
    if os.name == "posix":
        if status.st_uid != os.getuid() or stat.S_IMODE(status.st_mode) & 0o077:
            raise AuditError("audit path permissions are unsafe")


def _validate_event(operation: str, outcome: str, count: int) -> None:
    if not isinstance(operation, str) or operation not in _ALLOWED_OPERATIONS:
        raise AuditError("invalid audit operation")
    if not isinstance(outcome, str) or outcome not in _ALLOWED_OUTCOMES:
        raise AuditError("invalid audit outcome")
    if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= 100_000:
        raise AuditError("invalid audit item count")
