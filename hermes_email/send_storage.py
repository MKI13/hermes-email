"""Strict, non-pruning storage for send intents (schema 3).

All upgrades are transactional and require old send workers to be stopped.
Identity anchors are not backups: restore the whole ledger under operator
control, never delete the ledger to resolve an error.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from .send_files import (FileLease, SendBusyError, SendStorageError, open_private,
                         private_parent, private_status, sync_directory, verify_descriptor)

SCHEMA_VERSION = 3
APPLICATION_ID = 0x48455349
META_SQL = "CREATE TABLE meta (schema_version INTEGER NOT NULL CHECK(schema_version > 0))"
_FIELDS = (
    "operation_id TEXT PRIMARY KEY,draft_id TEXT NOT NULL,"
    "revision INTEGER NOT NULL CHECK(revision > 0),confirmation_id TEXT NOT NULL,"
    "request_digest TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN "
    "('dispatching','accepted','definite-failure','delivery-unknown')),"
)
TAIL_SQL = "created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(draft_id, revision)"
TABLE_V1 = "CREATE TABLE send_intents (" + _FIELDS + TAIL_SQL + ")"
TABLE_V2 = "CREATE TABLE send_intents (" + _FIELDS + "dispatcher_id TEXT," + TAIL_SQL + ")"
TABLE_ALTERED = TABLE_V1[:-1].replace(
    ",UNIQUE(draft_id, revision)", ", dispatcher_id TEXT,UNIQUE(draft_id, revision)"
) + ")"
IDENTITY_SQL = "CREATE TABLE ledger_identity (singleton INTEGER PRIMARY KEY CHECK(singleton=1),ledger_uuid TEXT NOT NULL)"
STATES = {"dispatching", "accepted", "definite-failure", "delivery-unknown"}


def _sql(value: str) -> str:
    return re.sub(r"\s+", "", value).lower()


def _token(value: object, *, minimum: int = 1, maximum: int = 128) -> bool:
    return isinstance(value, str) and minimum <= len(value) <= maximum and value.isascii() and all(33 <= ord(c) <= 126 for c in value)


class SecureSendDatabase:
    """Private validated SQLite transactions; no event retention or deletion."""

    def __init__(self, path: Path, *, max_database_bytes: int = 67_108_864, max_intents: int = 100_000) -> None:
        for value, low, high in ((max_database_bytes, 1_048_576, 268_435_456), (max_intents, 1, 1_000_000)):
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise SendStorageError("invalid send storage limit")
        self.path = Path(os.path.abspath(path))
        self.max_database_bytes = max_database_bytes
        self.max_intents = max_intents
        self._identity: tuple[int, int] | None = None
        self._parent_identity: tuple[int, int] | None = None
        self._closed = False
        self._lock = threading.RLock()
        self._pid = os.getpid()

    def close(self) -> None:
        with self._lock:
            self._closed = True

    @property
    def anchor_path(self) -> Path:
        return Path(str(self.path) + ".identity")

    def verify_path(self) -> None:
        if self._closed:
            raise SendStorageError("send storage is closed")
        parent = private_parent(self.path)
        if self._parent_identity is not None and self._parent_identity != parent:
            raise SendStorageError("send directory identity changed")
        self._parent_identity = parent
        status = self.path.lstat()
        private_status(status)
        identity = status.st_dev, status.st_ino
        if self._identity is not None and identity != self._identity:
            raise SendStorageError("send database identity changed")
        if status.st_size > self.max_database_bytes:
            raise SendStorageError("send database size limit exceeded")
        self._identity = identity
        for suffix in ("-journal", "-wal", "-shm"):
            try:
                sidecar = Path(str(self.path) + suffix).lstat()
            except FileNotFoundError:
                continue
            private_status(sidecar)
            if sidecar.st_size > self.max_database_bytes + 1_048_576:
                raise SendStorageError("send journal size limit exceeded")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        if self._pid != os.getpid():
            self._lock = threading.RLock()
            self._pid = os.getpid()
        with self._lock:
            connection = None
            fd = None
            guard = FileLease(Path(str(self.path) + ".guard"))
            try:
                if self._closed:
                    raise SendStorageError("send storage is closed")
                deadline = time.monotonic() + 5
                while not guard.acquire():
                    if time.monotonic() >= deadline:
                        raise SendBusyError("send database is busy")
                    time.sleep(0.01)
                anchor_exists = self.anchor_path.exists() or self.anchor_path.is_symlink()
                fd = open_private(self.path, create=not anchor_exists and self._identity is None)
                self.verify_path()
                connection = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True,
                                             timeout=5, isolation_level=None)
                verify_descriptor(self.path, fd)
                connection.execute("PRAGMA trusted_schema=OFF")
                connection.execute("PRAGMA temp_store=MEMORY")
                if connection.execute("PRAGMA journal_mode").fetchone() != ("delete",):
                    raise SendStorageError("send journal mode is incompatible")
                connection.execute("PRAGMA synchronous=EXTRA")
                if connection.execute("PRAGMA synchronous").fetchone() != (3,):
                    raise SendStorageError("send durability setting is unavailable")
                size = connection.execute("PRAGMA page_size").fetchone()[0]
                pages = connection.execute("PRAGMA page_count").fetchone()[0]
                maximum = self.max_database_bytes // size
                if pages > maximum or maximum < 1:
                    raise SendStorageError("send database size limit exceeded")
                if connection.execute(f"PRAGMA max_page_count={maximum}").fetchone() != (maximum,):
                    raise SendStorageError("send database page limit is unavailable")
                connection.execute("BEGIN IMMEDIATE")
                self._validate_and_migrate(connection)
                yield connection
                self.verify_path()
                verify_descriptor(self.path, fd)
                guard.verify()
                connection.commit()
                self.verify_path()
            except (OSError, sqlite3.Error, ValueError, OverflowError):
                raise SendStorageError("send storage transaction failed") from None
            finally:
                if connection is not None:
                    try:
                        if connection.in_transaction:
                            connection.rollback()
                    finally:
                        connection.close()
                if fd is not None:
                    os.close(fd)
                guard.close()

    def _validate_and_migrate(self, c: sqlite3.Connection) -> None:
        app = c.execute("PRAGMA application_id").fetchone()[0]
        version = c.execute("PRAGMA user_version").fetchone()[0]
        objects = {(kind, name): sql for kind, name, sql in c.execute("SELECT type,name,sql FROM sqlite_master")}
        new = not objects and (app, version) == (0, 0)
        if new:
            if self.anchor_path.exists():
                raise SendStorageError("send ledger was replaced or initialization was interrupted")
            c.execute(META_SQL)
            c.execute(TABLE_V2)
            c.execute("INSERT INTO meta VALUES (?)", (SCHEMA_VERSION,))
        else:
            if (app, version) not in {(0, 0), (APPLICATION_ID, SCHEMA_VERSION)}:
                raise SendStorageError("send database version is incompatible")
            expected = {("table", "meta"), ("table", "send_intents"),
                        ("index", "sqlite_autoindex_send_intents_1"), ("index", "sqlite_autoindex_send_intents_2")}
            if app == APPLICATION_ID:
                expected.add(("table", "ledger_identity"))
            if set(objects) != expected:
                raise SendStorageError("send database schema is incompatible")
            if _sql(objects[("table", "meta")]) != _sql(META_SQL):
                raise SendStorageError("send metadata schema is incompatible")
            metas = c.execute("SELECT schema_version FROM meta").fetchall()
            if len(metas) != 1 or metas[0][0] not in {1, 2, SCHEMA_VERSION}:
                raise SendStorageError("send metadata version is incompatible")
            legacy = metas[0][0]
            if (app == 0 and legacy not in {1, 2}) or (app == APPLICATION_ID and legacy != SCHEMA_VERSION):
                raise SendStorageError("send database identity is incompatible")
            allowed = { _sql(TABLE_V1) } if legacy == 1 else {_sql(TABLE_V2), _sql(TABLE_ALTERED)}
            if _sql(objects[("table", "send_intents")]) not in allowed:
                raise SendStorageError("send intent schema is incompatible")
            indexes = {tuple(r[2] for r in c.execute(f"PRAGMA index_info({name})")) for name in
                       ("sqlite_autoindex_send_intents_1", "sqlite_autoindex_send_intents_2")}
            if indexes != {("operation_id",), ("draft_id", "revision")}:
                raise SendStorageError("send uniqueness constraints are incompatible")
            if legacy == 1:
                c.execute("ALTER TABLE send_intents ADD COLUMN dispatcher_id TEXT")
        if c.execute("PRAGMA quick_check(1)").fetchone() != ("ok",):
            raise SendStorageError("send database integrity failed")
        count = c.execute("SELECT COUNT(*) FROM send_intents").fetchone()[0]
        if count > self.max_intents:
            raise SendStorageError("send intent capacity exceeded")
        for row in c.execute("SELECT operation_id,draft_id,revision,confirmation_id,request_digest,state,dispatcher_id,created_at,updated_at FROM send_intents"):
            self._validate_row(row)
        if app == 0:
            c.execute(IDENTITY_SQL)
            ledger_uuid = uuid.uuid4().hex
            c.execute("INSERT INTO ledger_identity VALUES (1,?)", (ledger_uuid,))
            c.execute("UPDATE meta SET schema_version=?", (SCHEMA_VERSION,))
            c.execute(f"PRAGMA application_id={APPLICATION_ID}")
            c.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            self._write_anchor(ledger_uuid)
        else:
            if _sql(objects[("table", "ledger_identity")]) != _sql(IDENTITY_SQL):
                raise SendStorageError("send ledger identity schema is incompatible")
            rows = c.execute("SELECT singleton,ledger_uuid FROM ledger_identity").fetchall()
            if len(rows) != 1 or rows[0][0] != 1 or not re.fullmatch(r"[0-9a-f]{32}", str(rows[0][1])):
                raise SendStorageError("send ledger identity is invalid")
            self._check_anchor(rows[0][1])

    @staticmethod
    def _validate_row(row: tuple) -> None:
        op, draft, revision, confirmation, digest, state, dispatcher, created, updated = row
        if not _token(op, minimum=16) or not _token(draft, maximum=512) or not _token(confirmation, minimum=16):
            raise SendStorageError("stored send identifier is invalid")
        if type(revision) is not int or not 1 <= revision <= 2_147_483_647:
            raise SendStorageError("stored send revision is invalid")
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None or state not in STATES:
            raise SendStorageError("stored send state is invalid")
        if dispatcher is not None and not _token(dispatcher):
            raise SendStorageError("stored send owner is invalid")
        for value in (created, updated):
            if not isinstance(value, str) or len(value) > 40 or datetime.fromisoformat(value).tzinfo is None:
                raise SendStorageError("stored send timestamp is invalid")

    def _anchor(self, ledger_uuid: str) -> bytes:
        assert self._identity is not None
        return json.dumps([1, *self._identity, ledger_uuid], separators=(",", ":")).encode("ascii")

    def _write_anchor(self, ledger_uuid: str) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(self.anchor_path, flags, 0o600)
        try:
            private_status(os.fstat(fd))
            data = self._anchor(ledger_uuid)
            if os.write(fd, data) != len(data):
                raise SendStorageError("send identity write failed")
            os.fsync(fd)
        finally:
            os.close(fd)
        sync_directory(self.path.parent)

    def _check_anchor(self, ledger_uuid: str) -> None:
        fd = open_private(self.anchor_path, create=False)
        try:
            if os.read(fd, 512) != self._anchor(ledger_uuid):
                raise SendStorageError("send ledger identity changed")
        finally:
            os.close(fd)
