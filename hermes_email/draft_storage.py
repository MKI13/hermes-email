"""Bounded local SQLite storage for reviewable email drafts."""

from __future__ import annotations

import base64
import errno
import hashlib
import json
import os
import re
import secrets
import sqlite3
import stat
import threading
import time
import unicodedata
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Final, Literal

from .addressing import (
    AddressValidationError,
    canonical_address,
    normalize_ascii_address,
    normalize_display_name,
)
from .config import DraftSettings
from .models import EmailAddress, EmailDraft, EmailDraftPage, EmailDraftSummary

_APPLICATION_ID: Final = 0x48454452
_SCHEMA_VERSION: Final = 1
_SQLITE_TIMEOUT_SECONDS: Final = 2
_MAX_DRAFT_ID: Final = 64
_MAX_OPERATION_ID: Final = 128
_MAX_CURSOR: Final = 512
_MAX_RECIPIENTS: Final = 50
_MAX_SUBJECT_CHARACTERS: Final = 500
_MAX_SUBJECT_BYTES: Final = 2_000
_MAX_BODY_CHARACTERS: Final = 20_000
_MAX_BODY_BYTES: Final = 80_000
_MAX_IN_REPLY_TO: Final = 512
_MAX_REVISION: Final = 2_147_483_647
_DRAFT_ID_PATTERN: Final = re.compile(r"draft_[A-Za-z0-9_-]{32}")
_OPERATION_ID_PATTERN: Final = re.compile(r"[A-Za-z0-9_-]{16,128}")

_CREATE_DRAFTS_SQL: Final = (
    "CREATE TABLE drafts ("
    "draft_id TEXT PRIMARY KEY,"
    "account_namespace TEXT NOT NULL,"
    "revision INTEGER NOT NULL CHECK(revision BETWEEN 1 AND 2147483647),"
    "state TEXT NOT NULL CHECK(state IN ('active','trashed')),"
    "subject TEXT NOT NULL,"
    "body_text TEXT NOT NULL,"
    "in_reply_to TEXT,"
    "created_at INTEGER NOT NULL CHECK(created_at >= 0),"
    "updated_at INTEGER NOT NULL CHECK(updated_at >= created_at),"
    "trashed_at INTEGER,"
    "CHECK((state='active' AND trashed_at IS NULL) OR "
    "(state='trashed' AND trashed_at IS NOT NULL AND trashed_at >= created_at))"
    ") WITHOUT ROWID"
)
_CREATE_RECIPIENTS_SQL: Final = (
    "CREATE TABLE draft_recipients ("
    "draft_id TEXT NOT NULL REFERENCES drafts(draft_id) ON DELETE CASCADE,"
    "kind TEXT NOT NULL CHECK(kind IN ('to','cc','bcc')),"
    "position INTEGER NOT NULL CHECK(position >= 0),"
    "address TEXT NOT NULL,"
    "display_name TEXT,"
    "PRIMARY KEY(draft_id,kind,position)"
    ") WITHOUT ROWID"
)
_CREATE_OPERATIONS_SQL: Final = (
    "CREATE TABLE draft_operations ("
    "account_namespace TEXT NOT NULL,"
    "operation_id TEXT NOT NULL,"
    "operation_kind TEXT NOT NULL "
    "CHECK(operation_kind IN ('create','update','trash','restore')),"
    "request_digest TEXT NOT NULL,"
    "draft_id TEXT NOT NULL,"
    "result_revision INTEGER NOT NULL "
    "CHECK(result_revision BETWEEN 1 AND 2147483647),"
    "completed_at INTEGER NOT NULL CHECK(completed_at >= 0),"
    "PRIMARY KEY(account_namespace,operation_id)"
    ") WITHOUT ROWID"
)
_CREATE_UPDATED_INDEX_SQL: Final = (
    "CREATE INDEX drafts_updated ON drafts(state,updated_at DESC,draft_id DESC)"
)
_EXPECTED_DEFINITIONS: Final = {
    "drafts": _CREATE_DRAFTS_SQL,
    "draft_recipients": _CREATE_RECIPIENTS_SQL,
    "draft_operations": _CREATE_OPERATIONS_SQL,
    "drafts_updated": _CREATE_UPDATED_INDEX_SQL,
}
_EXPECTED_COLUMNS: Final = {
    "drafts": (
        ("draft_id", "TEXT", 1, 1),
        ("account_namespace", "TEXT", 1, 0),
        ("revision", "INTEGER", 1, 0),
        ("state", "TEXT", 1, 0),
        ("subject", "TEXT", 1, 0),
        ("body_text", "TEXT", 1, 0),
        ("in_reply_to", "TEXT", 0, 0),
        ("created_at", "INTEGER", 1, 0),
        ("updated_at", "INTEGER", 1, 0),
        ("trashed_at", "INTEGER", 0, 0),
    ),
    "draft_recipients": (
        ("draft_id", "TEXT", 1, 1),
        ("kind", "TEXT", 1, 2),
        ("position", "INTEGER", 1, 3),
        ("address", "TEXT", 1, 0),
        ("display_name", "TEXT", 0, 0),
    ),
    "draft_operations": (
        ("account_namespace", "TEXT", 1, 1),
        ("operation_id", "TEXT", 1, 2),
        ("operation_kind", "TEXT", 1, 0),
        ("request_digest", "TEXT", 1, 0),
        ("draft_id", "TEXT", 1, 0),
        ("result_revision", "INTEGER", 1, 0),
        ("completed_at", "INTEGER", 1, 0),
    ),
}


class DraftError(RuntimeError):
    """Base class for fixed local draft failures."""


class DraftValidationError(DraftError):
    """Raised when draft input is malformed or outside a fixed bound."""


class DraftNotFoundError(DraftError):
    """Raised when an opaque draft ID does not identify a usable draft."""


class DraftConflictError(DraftError):
    """Raised when a revision or operation ID does not match current state."""

    def __init__(self, message: str, *, current_revision: int | None = None) -> None:
        super().__init__(message)
        self.current_revision = current_revision


class DraftStateError(DraftError):
    """Raised when a draft is active or trashed contrary to the operation."""


class DraftLimitError(DraftError):
    """Raised when a bounded draft or operation capacity is exhausted."""


class DraftStorageError(DraftError):
    """Base class for fixed draft database failures."""


class DraftStorageClosedError(DraftStorageError):
    """Raised after the owning plugin closes draft storage."""


class DraftStorageSecurityError(DraftStorageError):
    """Raised for an unsafe durable path object."""


class DraftStorageSchemaError(DraftStorageError):
    """Raised for corrupt, foreign, or incompatible draft storage."""


class DraftStorageResourceError(DraftStorageError):
    """Raised when a configured or platform resource bound is exhausted."""


class DraftStorageBusyError(DraftStorageError):
    """Raised when another process holds draft storage past the fixed timeout."""


class DraftStorageUnavailableError(DraftStorageError):
    """Raised for other bounded local storage failures."""


@dataclass(frozen=True, slots=True)
class DraftMutation:
    """Content-free receipt for one definite local draft mutation."""

    draft_id: str
    revision: int
    replayed: bool = False


class SqliteDraftStore:
    """Own one isolated, profile-scoped local draft database."""

    def __init__(
        self,
        path: Path,
        settings: DraftSettings,
        *,
        clock: Callable[[], int] = lambda: time.time_ns() // 1_000,
        id_factory: Callable[[], str] = lambda: "draft_" + secrets.token_urlsafe(24),
    ) -> None:
        self.path = Path(path)
        self.settings = settings
        self._clock = clock
        self._id_factory = id_factory
        self._lock = threading.RLock()
        self._closed = False
        self._initialized = False

    def close(self) -> None:
        """Wait for an active operation and reject every later operation."""
        with self._lock:
            self._closed = True

    def create_draft(self, draft: EmailDraft, operation_id: str) -> DraftMutation:
        """Create one validated draft with an idempotent operation receipt."""
        content = validate_draft_content(draft)
        operation = _operation_id(operation_id)
        digest = _request_digest("create", self.settings.account_namespace, content)
        with self._lock:
            self._ensure_open()
            connection = self._connect()
            try:
                self._initialize(connection)
                connection.execute("BEGIN IMMEDIATE")
                replay = self._operation_replay(connection, operation, "create", digest)
                if replay is not None:
                    connection.commit()
                    return replay
                self._require_operation_capacity(connection)
                count = connection.execute(
                    "SELECT COUNT(*) FROM drafts WHERE account_namespace=?",
                    (self.settings.account_namespace,),
                ).fetchone()
                if count is None or not isinstance(count[0], int):
                    raise DraftStorageSchemaError("draft count is invalid")
                if count[0] >= self.settings.max_drafts:
                    raise DraftLimitError("draft capacity is exhausted")
                now = _timestamp(self._clock())
                draft_id = self._insert_new_draft(connection, content, now)
                self._insert_operation(
                    connection, operation, "create", digest, draft_id, 1, now
                )
                connection.commit()
                return DraftMutation(draft_id=draft_id, revision=1)
            except DraftError:
                connection.rollback()
                raise
            except sqlite3.DatabaseError as error:
                connection.rollback()
                self._raise_database_error(error)
            finally:
                connection.close()

    def update_draft(
        self,
        draft_id: str,
        expected_revision: int,
        draft: EmailDraft,
        operation_id: str,
    ) -> DraftMutation:
        """Atomically replace one active draft at an exact revision."""
        identifier = _draft_id(draft_id)
        revision = _revision(expected_revision)
        content = validate_draft_content(draft)
        operation = _operation_id(operation_id)
        digest = _request_digest(
            "update", self.settings.account_namespace, identifier, revision, content
        )
        with self._lock:
            self._ensure_open()
            connection = self._connect()
            try:
                self._initialize(connection)
                connection.execute("BEGIN IMMEDIATE")
                replay = self._operation_replay(connection, operation, "update", digest)
                if replay is not None:
                    connection.commit()
                    return replay
                self._require_operation_capacity(connection)
                row = self._mutation_row(connection, identifier)
                self._require_active_revision(row, revision)
                next_revision = revision + 1
                if next_revision > _MAX_REVISION:
                    raise DraftLimitError("draft revision capacity is exhausted")
                now = max(_timestamp(self._clock()), int(row[2]) + 1)
                cursor = connection.execute(
                    "UPDATE drafts SET revision=?,subject=?,body_text=?,in_reply_to=?,"
                    "updated_at=? WHERE draft_id=? AND account_namespace=? "
                    "AND revision=? AND state='active'",
                    (
                        next_revision,
                        content.subject,
                        content.body_text,
                        content.in_reply_to,
                        now,
                        identifier,
                        self.settings.account_namespace,
                        revision,
                    ),
                )
                if cursor.rowcount != 1:
                    raise DraftConflictError("draft changed concurrently")
                connection.execute(
                    "DELETE FROM draft_recipients WHERE draft_id=?", (identifier,)
                )
                self._insert_recipients(connection, identifier, content)
                self._insert_operation(
                    connection,
                    operation,
                    "update",
                    digest,
                    identifier,
                    next_revision,
                    now,
                )
                connection.commit()
                return DraftMutation(identifier, next_revision)
            except DraftError:
                connection.rollback()
                raise
            except sqlite3.DatabaseError as error:
                connection.rollback()
                self._raise_database_error(error)
            finally:
                connection.close()

    def trash_draft(
        self, draft_id: str, expected_revision: int, operation_id: str
    ) -> DraftMutation:
        """Move one exact active revision into reversible local trash."""
        return self._change_state(
            draft_id, expected_revision, operation_id, "active", "trashed", "trash"
        )

    def restore_draft(
        self, draft_id: str, expected_revision: int, operation_id: str
    ) -> DraftMutation:
        """Restore one exact trashed revision to the active draft list."""
        return self._change_state(
            draft_id, expected_revision, operation_id, "trashed", "active", "restore"
        )

    def get_draft(self, draft_id: str) -> EmailDraft | None:
        """Return one active draft, excluding reversible trash."""
        identifier = _draft_id(draft_id)
        with self._lock:
            self._ensure_open()
            connection = self._connect()
            try:
                self._initialize(connection)
                row = connection.execute(
                    "SELECT draft_id,revision,subject,body_text,in_reply_to,created_at,updated_at "
                    "FROM drafts WHERE draft_id=? AND account_namespace=? AND state='active'",
                    (identifier, self.settings.account_namespace),
                ).fetchone()
                if row is None:
                    return None
                return self._draft_from_row(connection, row)
            except DraftError:
                raise
            except sqlite3.DatabaseError as error:
                self._raise_database_error(error)
            finally:
                connection.close()

    def get_active_revision(self, draft_id: str, expected_revision: int) -> EmailDraft:
        """Return one immutable active revision snapshot for a technical gate."""
        identifier = _draft_id(draft_id)
        revision = _revision(expected_revision)
        with self._lock:
            self._ensure_open()
            connection = self._connect()
            try:
                self._initialize(connection)
                connection.execute("BEGIN")
                mutation = self._mutation_row(connection, identifier)
                self._require_active_revision(mutation, revision)
                row = connection.execute(
                    "SELECT draft_id,revision,subject,body_text,in_reply_to,created_at,updated_at "
                    "FROM drafts WHERE draft_id=? AND account_namespace=? "
                    "AND state='active' AND revision=?",
                    (identifier, self.settings.account_namespace, revision),
                ).fetchone()
                if row is None:
                    raise DraftConflictError("draft changed concurrently")
                result = self._draft_from_row(connection, row)
                connection.commit()
                return result
            except DraftError:
                connection.rollback()
                raise
            except sqlite3.DatabaseError as error:
                connection.rollback()
                self._raise_database_error(error)
            finally:
                connection.close()

    def list_drafts(
        self,
        *,
        state: Literal["active", "trashed"] = "active",
        limit: int = 10,
        cursor: str | None = None,
    ) -> EmailDraftPage:
        """Return one body-free newest-updated summary page."""
        if state not in {"active", "trashed"}:
            raise DraftValidationError("draft state is invalid")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 25:
            raise DraftValidationError("draft page limit is invalid")
        boundary = _decode_cursor(cursor) if cursor is not None else None
        with self._lock:
            self._ensure_open()
            connection = self._connect()
            try:
                self._initialize(connection)
                parameters: list[object] = [state, self.settings.account_namespace]
                where = "state=? AND account_namespace=?"
                if boundary is not None:
                    where += " AND (updated_at < ? OR (updated_at=? AND draft_id < ?))"
                    parameters.extend((boundary[0], boundary[0], boundary[1]))
                parameters.append(limit + 1)
                rows = connection.execute(
                    "SELECT draft_id,revision,state,subject,LENGTH(body_text),in_reply_to,"
                    "created_at,updated_at,(SELECT COUNT(*) FROM draft_recipients r "
                    "WHERE r.draft_id=drafts.draft_id) FROM drafts WHERE "
                    + where
                    + " ORDER BY updated_at DESC,draft_id DESC LIMIT ?",
                    tuple(parameters),
                ).fetchall()
                visible = rows[:limit]
                summaries = tuple(_summary_from_row(row) for row in visible)
                next_cursor = None
                if len(rows) > limit and visible:
                    next_cursor = _encode_cursor(int(visible[-1][7]), str(visible[-1][0]))
                return EmailDraftPage(summaries, next_cursor)
            except DraftError:
                raise
            except sqlite3.DatabaseError as error:
                self._raise_database_error(error)
            finally:
                connection.close()

    def count_drafts(self, *, state: str | None = None) -> int:
        """Return a bounded test/operator count without exposing content."""
        if state is not None and state not in {"active", "trashed"}:
            raise DraftValidationError("draft state is invalid")
        with self._lock:
            self._ensure_open()
            connection = self._connect()
            try:
                self._initialize(connection)
                if state is None:
                    row = connection.execute(
                        "SELECT COUNT(*) FROM drafts WHERE account_namespace=?",
                        (self.settings.account_namespace,),
                    ).fetchone()
                else:
                    row = connection.execute(
                        "SELECT COUNT(*) FROM drafts WHERE account_namespace=? AND state=?",
                        (self.settings.account_namespace, state),
                    ).fetchone()
                if row is None or not isinstance(row[0], int):
                    raise DraftStorageSchemaError("draft count is invalid")
                return row[0]
            except DraftError:
                raise
            except sqlite3.DatabaseError as error:
                self._raise_database_error(error)
            finally:
                connection.close()

    def _change_state(
        self,
        draft_id: str,
        expected_revision: int,
        operation_id: str,
        source_state: str,
        target_state: str,
        operation_kind: str,
    ) -> DraftMutation:
        identifier = _draft_id(draft_id)
        revision = _revision(expected_revision)
        operation = _operation_id(operation_id)
        digest = _request_digest(
            operation_kind, self.settings.account_namespace, identifier, revision
        )
        with self._lock:
            self._ensure_open()
            connection = self._connect()
            try:
                self._initialize(connection)
                connection.execute("BEGIN IMMEDIATE")
                replay = self._operation_replay(
                    connection, operation, operation_kind, digest
                )
                if replay is not None:
                    connection.commit()
                    return replay
                self._require_operation_capacity(connection)
                row = self._mutation_row(connection, identifier)
                if str(row[1]) != source_state:
                    raise DraftStateError("draft state does not allow this operation")
                if int(row[0]) != revision:
                    raise DraftConflictError(
                        "draft revision changed", current_revision=int(row[0])
                    )
                next_revision = revision + 1
                if next_revision > _MAX_REVISION:
                    raise DraftLimitError("draft revision capacity is exhausted")
                now = max(_timestamp(self._clock()), int(row[2]) + 1)
                trashed_at = now if target_state == "trashed" else None
                cursor = connection.execute(
                    "UPDATE drafts SET revision=?,state=?,updated_at=?,trashed_at=? "
                    "WHERE draft_id=? AND account_namespace=? AND revision=? AND state=?",
                    (
                        next_revision,
                        target_state,
                        now,
                        trashed_at,
                        identifier,
                        self.settings.account_namespace,
                        revision,
                        source_state,
                    ),
                )
                if cursor.rowcount != 1:
                    raise DraftConflictError("draft changed concurrently")
                self._insert_operation(
                    connection,
                    operation,
                    operation_kind,
                    digest,
                    identifier,
                    next_revision,
                    now,
                )
                connection.commit()
                return DraftMutation(identifier, next_revision)
            except DraftError:
                connection.rollback()
                raise
            except sqlite3.DatabaseError as error:
                connection.rollback()
                self._raise_database_error(error)
            finally:
                connection.close()

    def _insert_new_draft(
        self, connection: sqlite3.Connection, draft: EmailDraft, now: int
    ) -> str:
        for _attempt in range(3):
            candidate = _draft_id(self._id_factory())
            try:
                connection.execute(
                    "INSERT INTO drafts VALUES (?,?,1,'active',?,?,?,?,?,NULL)",
                    (
                        candidate,
                        self.settings.account_namespace,
                        draft.subject,
                        draft.body_text,
                        draft.in_reply_to,
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError:
                continue
            self._insert_recipients(connection, candidate, draft)
            return candidate
        raise DraftStorageResourceError("could not allocate a unique draft ID")

    @staticmethod
    def _insert_recipients(
        connection: sqlite3.Connection, draft_id: str, draft: EmailDraft
    ) -> None:
        rows = []
        for kind, addresses in (
            ("to", draft.recipients),
            ("cc", draft.cc),
            ("bcc", draft.bcc),
        ):
            rows.extend(
                (draft_id, kind, position, address.address, address.display_name)
                for position, address in enumerate(addresses)
            )
        connection.executemany(
            "INSERT INTO draft_recipients VALUES (?,?,?,?,?)", rows
        )

    def _draft_from_row(self, connection: sqlite3.Connection, row: tuple) -> EmailDraft:
        if (
            len(row) != 7
            or not isinstance(row[0], str)
            or not isinstance(row[1], int)
            or not isinstance(row[2], str)
            or not isinstance(row[3], str)
            or (row[4] is not None and not isinstance(row[4], str))
            or not isinstance(row[5], int)
            or not isinstance(row[6], int)
        ):
            raise DraftStorageSchemaError("draft record is invalid")
        recipient_rows = connection.execute(
            "SELECT kind,position,address,display_name FROM draft_recipients "
            "WHERE draft_id=? ORDER BY CASE kind WHEN 'to' THEN 0 WHEN 'cc' THEN 1 "
            "ELSE 2 END,position",
            (row[0],),
        ).fetchall()
        grouped: dict[str, list[EmailAddress]] = {"to": [], "cc": [], "bcc": []}
        for kind, position, address, display_name in recipient_rows:
            if (
                not isinstance(kind, str)
                or not isinstance(position, int)
                or not isinstance(address, str)
                or (display_name is not None and not isinstance(display_name, str))
            ):
                raise DraftStorageSchemaError("draft recipient record is invalid")
            values = grouped.get(kind)
            if values is None or position != len(values):
                raise DraftStorageSchemaError("draft recipient ordering is invalid")
            values.append(EmailAddress(str(address), display_name))
        try:
            content = validate_draft_content(
                EmailDraft(
                    recipients=tuple(grouped["to"]),
                    cc=tuple(grouped["cc"]),
                    bcc=tuple(grouped["bcc"]),
                    subject=row[2],
                    body_text=row[3],
                    in_reply_to=row[4],
                )
            )
        except DraftValidationError:
            raise DraftStorageSchemaError("stored draft content is invalid") from None
        return replace(
            content,
            draft_id=_stored_draft_id(row[0]),
            revision=_stored_revision(row[1]),
            created_at=_datetime(int(row[5])),
            updated_at=_datetime(int(row[6])),
        )

    def _mutation_row(self, connection: sqlite3.Connection, draft_id: str) -> tuple:
        row = connection.execute(
            "SELECT revision,state,updated_at FROM drafts "
            "WHERE draft_id=? AND account_namespace=?",
            (draft_id, self.settings.account_namespace),
        ).fetchone()
        if row is None:
            raise DraftNotFoundError("draft was not found")
        if (
            len(row) != 3
            or not isinstance(row[0], int)
            or not isinstance(row[1], str)
            or not isinstance(row[2], int)
        ):
            raise DraftStorageSchemaError("draft record is invalid")
        return row

    @staticmethod
    def _require_active_revision(row: tuple, expected_revision: int) -> None:
        if str(row[1]) != "active":
            raise DraftStateError("trashed draft cannot be updated")
        if int(row[0]) != expected_revision:
            raise DraftConflictError(
                "draft revision changed", current_revision=int(row[0])
            )

    def _operation_replay(
        self,
        connection: sqlite3.Connection,
        operation_id: str,
        operation_kind: str,
        request_digest: str,
    ) -> DraftMutation | None:
        row = connection.execute(
            "SELECT operation_kind,request_digest,draft_id,result_revision "
            "FROM draft_operations WHERE account_namespace=? AND operation_id=?",
            (self.settings.account_namespace, operation_id),
        ).fetchone()
        if row is None:
            return None
        if (
            len(row) != 4
            or not isinstance(row[0], str)
            or not isinstance(row[1], str)
            or not isinstance(row[2], str)
            or not isinstance(row[3], int)
        ):
            raise DraftStorageSchemaError("draft operation record is invalid")
        if row[0] != operation_kind or not secrets.compare_digest(
            row[1], request_digest
        ):
            raise DraftConflictError("operation ID was already used")
        return DraftMutation(
            _stored_draft_id(row[2]), _stored_revision(row[3]), replayed=True
        )

    def _require_operation_capacity(self, connection: sqlite3.Connection) -> None:
        row = connection.execute(
            "SELECT COUNT(*) FROM draft_operations WHERE account_namespace=?",
            (self.settings.account_namespace,),
        ).fetchone()
        if row is None or not isinstance(row[0], int):
            raise DraftStorageSchemaError("draft operation count is invalid")
        if row[0] >= self.settings.max_operations:
            raise DraftLimitError("draft operation capacity is exhausted")

    def _insert_operation(
        self,
        connection: sqlite3.Connection,
        operation_id: str,
        operation_kind: str,
        request_digest: str,
        draft_id: str,
        result_revision: int,
        completed_at: int,
    ) -> None:
        connection.execute(
            "INSERT INTO draft_operations VALUES (?,?,?,?,?,?,?)",
            (
                self.settings.account_namespace,
                operation_id,
                operation_kind,
                request_digest,
                draft_id,
                result_revision,
                completed_at,
            ),
        )

    def _ensure_open(self) -> None:
        if self._closed:
            raise DraftStorageClosedError("draft storage is closed")

    def _connect(self) -> sqlite3.Connection:
        expected_identity = self._prepare_private_path()
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                self.path,
                timeout=_SQLITE_TIMEOUT_SECONDS,
                isolation_level=None,
            )
            current = self.path.lstat()
            if (
                self.path.is_symlink()
                or not stat.S_ISREG(current.st_mode)
                or current.st_nlink != 1
                or (current.st_dev, current.st_ino) != expected_identity
            ):
                raise DraftStorageSecurityError("draft database identity changed")
            connection.execute(f"PRAGMA busy_timeout={_SQLITE_TIMEOUT_SECONDS * 1000}")
            return connection
        except DraftError:
            if connection is not None:
                connection.close()
            raise
        except sqlite3.DatabaseError as error:
            if connection is not None:
                connection.close()
            self._raise_database_error(error)
        except OSError:
            if connection is not None:
                connection.close()
            _raise_draft(DraftStorageUnavailableError("draft path is unavailable"))

    def _prepare_private_path(self) -> tuple[int, int]:
        parent = self.path.parent
        try:
            parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            parent_status = parent.lstat()
            if not stat.S_ISDIR(parent_status.st_mode) or parent.is_symlink():
                raise DraftStorageSecurityError("draft directory is not private")
            _verify_private_owner(parent_status, is_directory=True)
            flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
            if self.path.is_symlink():
                raise DraftStorageSecurityError("draft database is not a regular file")
            try:
                descriptor = os.open(self.path, flags)
                created = False
            except FileNotFoundError:
                try:
                    descriptor = os.open(
                        self.path, flags | os.O_CREAT | os.O_EXCL, 0o600
                    )
                    created = True
                except FileExistsError:
                    descriptor = os.open(self.path, flags)
                    created = False
            try:
                file_status = os.fstat(descriptor)
                if not stat.S_ISREG(file_status.st_mode) or file_status.st_nlink != 1:
                    raise DraftStorageSecurityError(
                        "draft database is not a private regular file"
                    )
                _verify_private_owner(file_status, is_directory=False)
                if created and os.name != "nt" and stat.S_IMODE(file_status.st_mode) != 0o600:
                    raise DraftStorageSecurityError("draft database permissions are unsafe")
            finally:
                os.close(descriptor)
            return (file_status.st_dev, file_status.st_ino)
        except DraftError:
            raise
        except OSError as error:
            if error.errno == errno.ELOOP:
                _raise_draft(DraftStorageSecurityError("draft path is unsafe"))
            if error.errno in {
                errno.ENOSPC,
                errno.EMFILE,
                errno.ENFILE,
                getattr(errno, "EDQUOT", errno.ENOSPC),
            }:
                _raise_draft(DraftStorageResourceError("draft storage resource unavailable"))
            _raise_draft(DraftStorageUnavailableError("draft path is unavailable"))

    def _initialize(self, connection: sqlite3.Connection) -> None:
        if self._initialized:
            self._verify_database(connection)
            self._configure_connection(connection)
            return
        try:
            connection.execute("BEGIN EXCLUSIVE")
            version = _pragma_integer(connection, "user_version")
            application_id = _pragma_integer(connection, "application_id")
            objects = _database_objects(connection)
            if version == 0 and application_id == 0 and not objects:
                connection.execute(_CREATE_DRAFTS_SQL)
                connection.execute(_CREATE_RECIPIENTS_SQL)
                connection.execute(_CREATE_OPERATIONS_SQL)
                connection.execute(_CREATE_UPDATED_INDEX_SQL)
                connection.execute(f"PRAGMA application_id={_APPLICATION_ID}")
                connection.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
            elif version != _SCHEMA_VERSION or application_id != _APPLICATION_ID:
                raise DraftStorageSchemaError("draft database identity is incompatible")
            self._verify_database(connection)
            connection.commit()
            self._configure_connection(connection)
            self._initialized = True
        except DraftError:
            connection.rollback()
            raise
        except sqlite3.DatabaseError as error:
            connection.rollback()
            self._raise_database_error(error)

    def _verify_database(self, connection: sqlite3.Connection) -> None:
        if _pragma_integer(connection, "application_id") != _APPLICATION_ID:
            raise DraftStorageSchemaError("draft database identity is incompatible")
        if _pragma_integer(connection, "user_version") != _SCHEMA_VERSION:
            raise DraftStorageSchemaError("draft database version is incompatible")
        objects = _database_objects(connection)
        expected_objects = {
            ("table", "drafts"),
            ("table", "draft_recipients"),
            ("table", "draft_operations"),
            ("index", "drafts_updated"),
        }
        if objects != expected_objects:
            raise DraftStorageSchemaError("draft database schema is invalid")
        for table, expected in _EXPECTED_COLUMNS.items():
            columns = connection.execute(f"PRAGMA table_info({table})").fetchall()
            contract = tuple((row[1], row[2], row[3], row[5]) for row in columns)
            if contract != expected:
                raise DraftStorageSchemaError("draft database schema is invalid")
        definitions = dict(
            connection.execute(
                "SELECT name,sql FROM sqlite_master WHERE name IN "
                "('drafts','draft_recipients','draft_operations','drafts_updated')"
            ).fetchall()
        )
        if definitions != _EXPECTED_DEFINITIONS:
            raise DraftStorageSchemaError("draft database schema is invalid")
        foreign_keys = connection.execute(
            "PRAGMA foreign_key_list(draft_recipients)"
        ).fetchall()
        if len(foreign_keys) != 1 or tuple(foreign_keys[0][2:7]) != (
            "drafts",
            "draft_id",
            "draft_id",
            "NO ACTION",
            "CASCADE",
        ):
            raise DraftStorageSchemaError("draft database foreign key is invalid")
        self._enforce_page_limit(connection)
        if connection.execute("PRAGMA quick_check(1)").fetchone() != ("ok",):
            raise DraftStorageSchemaError("draft database integrity failed")

    def _configure_connection(self, connection: sqlite3.Connection) -> None:
        connection.enable_load_extension(False)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("PRAGMA secure_delete=ON")
        connection.execute("PRAGMA temp_store=MEMORY")
        journal = connection.execute("PRAGMA journal_mode=DELETE").fetchone()
        if journal is None or str(journal[0]).casefold() != "delete":
            raise DraftStorageUnavailableError("draft journal mode is unavailable")
        connection.execute("PRAGMA synchronous=FULL")
        if (
            _pragma_integer(connection, "foreign_keys") != 1
            or _pragma_integer(connection, "trusted_schema") != 0
            or _pragma_integer(connection, "secure_delete") != 1
            or _pragma_integer(connection, "temp_store") != 2
            or _pragma_integer(connection, "synchronous") != 2
        ):
            raise DraftStorageUnavailableError("draft safety settings are unavailable")

    def _enforce_page_limit(self, connection: sqlite3.Connection) -> None:
        page_size = _pragma_integer(connection, "page_size")
        page_count = _pragma_integer(connection, "page_count")
        if page_size <= 0:
            raise DraftStorageSchemaError("draft page metadata is invalid")
        maximum_pages = max(1, self.settings.max_database_bytes // page_size)
        if page_count > maximum_pages:
            raise DraftStorageResourceError("draft database exceeds its size limit")
        if connection.execute(f"PRAGMA max_page_count={maximum_pages}").fetchone() != (
            maximum_pages,
        ):
            raise DraftStorageResourceError("draft size limit is unavailable")

    @staticmethod
    def _raise_database_error(error: sqlite3.DatabaseError) -> None:
        code = getattr(error, "sqlite_errorcode", None)
        primary = code & 0xFF if isinstance(code, int) else None
        if primary in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
            _raise_draft(DraftStorageBusyError("draft storage is busy"))
        if primary in {sqlite3.SQLITE_FULL, sqlite3.SQLITE_NOMEM, sqlite3.SQLITE_TOOBIG}:
            _raise_draft(DraftStorageResourceError("draft storage capacity is exhausted"))
        if primary in {sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB}:
            _raise_draft(DraftStorageSchemaError("draft database is invalid"))
        _raise_draft(DraftStorageUnavailableError("draft storage is unavailable"))


def validate_draft_content(draft: EmailDraft) -> EmailDraft:
    """Validate, normalize, and own draft content without assigning authority."""
    if not isinstance(draft, EmailDraft):
        raise DraftValidationError("draft content is invalid")
    if (
        draft.draft_id is not None
        or draft.revision is not None
        or draft.created_at is not None
        or draft.updated_at is not None
    ):
        raise DraftValidationError("draft content contains storage-managed fields")
    recipients = _addresses(draft.recipients, "to")
    cc = _addresses(draft.cc, "cc")
    bcc = _addresses(draft.bcc, "bcc")
    all_addresses = recipients + cc + bcc
    if not all_addresses:
        raise DraftValidationError("at least one recipient is required")
    if len(all_addresses) > _MAX_RECIPIENTS:
        raise DraftValidationError("recipient limit exceeded")
    canonical = [_canonical_address(address.address) for address in all_addresses]
    if len(set(canonical)) != len(canonical):
        raise DraftValidationError("duplicate recipient is not allowed")
    subject = _text(
        draft.subject,
        "subject",
        max_characters=_MAX_SUBJECT_CHARACTERS,
        max_bytes=_MAX_SUBJECT_BYTES,
        allow_body_controls=False,
    )
    body = _text(
        draft.body_text.replace("\r\n", "\n").replace("\r", "\n")
        if isinstance(draft.body_text, str)
        else draft.body_text,
        "body",
        max_characters=_MAX_BODY_CHARACTERS,
        max_bytes=_MAX_BODY_BYTES,
        allow_body_controls=True,
    )
    in_reply_to = None
    if draft.in_reply_to is not None:
        in_reply_to = _text(
            draft.in_reply_to,
            "in_reply_to",
            max_characters=_MAX_IN_REPLY_TO,
            max_bytes=2_048,
            allow_body_controls=False,
            require_nonempty=True,
        )
    return EmailDraft(
        recipients=recipients,
        cc=cc,
        bcc=bcc,
        subject=subject,
        body_text=body,
        in_reply_to=in_reply_to,
    )


def _addresses(values: object, name: str) -> tuple[EmailAddress, ...]:
    if not isinstance(values, (tuple, list)):
        raise DraftValidationError(f"{name} recipients are invalid")
    result = []
    for value in values:
        if not isinstance(value, EmailAddress):
            raise DraftValidationError(f"{name} recipient is invalid")
        address = _email_address(value.address)
        display_name = None
        if value.display_name is not None:
            try:
                display_name = normalize_display_name(value.display_name)
            except AddressValidationError:
                raise DraftValidationError("draft display name is invalid") from None
        result.append(EmailAddress(address, display_name))
    return tuple(result)


def _email_address(value: object) -> str:
    try:
        return normalize_ascii_address(value)
    except AddressValidationError:
        raise DraftValidationError("email address syntax is unsupported") from None


def _canonical_address(address: str) -> str:
    return canonical_address(address)


def _text(
    value: object,
    name: str,
    *,
    max_characters: int,
    max_bytes: int,
    allow_body_controls: bool,
    require_nonempty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise DraftValidationError(f"draft {name} must be text")
    normalized = unicodedata.normalize("NFC", value)
    if require_nonempty and not normalized:
        raise DraftValidationError(f"draft {name} must not be empty")
    if len(normalized) > max_characters or len(normalized.encode("utf-8")) > max_bytes:
        raise DraftValidationError(f"draft {name} exceeds its limit")
    for character in normalized:
        category = unicodedata.category(character)
        if allow_body_controls and character in {"\n", "\t"}:
            continue
        if category in {"Cc", "Cf", "Cs"}:
            raise DraftValidationError(f"draft {name} contains unsupported controls")
    return normalized


def _stored_text(value: object, name: str, **limits: object) -> str:
    try:
        return _text(value, name, **limits)  # type: ignore[arg-type]
    except DraftValidationError:
        raise DraftStorageSchemaError(f"stored draft {name} is invalid") from None


def _draft_id(value: object) -> str:
    if not isinstance(value, str) or _DRAFT_ID_PATTERN.fullmatch(value) is None:
        raise DraftValidationError("draft ID is invalid")
    return value


def _stored_draft_id(value: object) -> str:
    try:
        return _draft_id(value)
    except DraftValidationError:
        raise DraftStorageSchemaError("stored draft ID is invalid") from None


def _operation_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) > _MAX_OPERATION_ID
        or _OPERATION_ID_PATTERN.fullmatch(value) is None
    ):
        raise DraftValidationError("draft operation ID is invalid")
    return value


def _revision(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= _MAX_REVISION:
        raise DraftValidationError("draft revision is invalid")
    return value


def _stored_revision(value: object) -> int:
    try:
        return _revision(value)
    except DraftValidationError:
        raise DraftStorageSchemaError("stored draft revision is invalid") from None


def _timestamp(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DraftStorageUnavailableError("draft clock is unavailable")
    return value


def _request_digest(*values: object) -> str:
    def owned(value: object) -> object:
        if isinstance(value, EmailDraft):
            return {
                "to": [owned(address) for address in value.recipients],
                "cc": [owned(address) for address in value.cc],
                "bcc": [owned(address) for address in value.bcc],
                "subject": value.subject,
                "body_text": value.body_text,
                "in_reply_to": value.in_reply_to,
            }
        if isinstance(value, EmailAddress):
            return {"address": value.address, "display_name": value.display_name}
        return value

    encoded = json.dumps(
        [owned(value) for value in values],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _encode_cursor(updated_at: int, draft_id: str) -> str:
    raw = f"v1:{updated_at}:{draft_id}".encode("ascii")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(value: object) -> tuple[int, str]:
    if not isinstance(value, str) or not 1 <= len(value) <= _MAX_CURSOR:
        raise DraftValidationError("draft cursor is invalid")
    try:
        padding = "=" * (-len(value) % 4)
        raw = base64.b64decode(value + padding, altchars=b"-_", validate=True).decode(
            "ascii"
        )
        version, encoded_time, draft_id = raw.split(":", 2)
        if version != "v1" or not encoded_time.isdigit():
            raise ValueError
        timestamp = int(encoded_time)
        identifier = _draft_id(draft_id)
        if _encode_cursor(timestamp, identifier) != value:
            raise ValueError
        return timestamp, identifier
    except (UnicodeError, ValueError, TypeError):
        raise DraftValidationError("draft cursor is invalid") from None


def _summary_from_row(row: tuple) -> EmailDraftSummary:
    if (
        len(row) != 9
        or not isinstance(row[0], str)
        or not isinstance(row[1], int)
        or not isinstance(row[2], str)
        or not isinstance(row[3], str)
        or not isinstance(row[4], int)
        or (row[5] is not None and not isinstance(row[5], str))
        or not isinstance(row[6], int)
        or not isinstance(row[7], int)
        or not isinstance(row[8], int)
    ):
        raise DraftStorageSchemaError("draft summary is invalid")
    state = str(row[2])
    body_characters = int(row[4])
    recipients = int(row[8])
    if (
        state not in {"active", "trashed"}
        or not 0 <= body_characters <= _MAX_BODY_CHARACTERS
        or not 1 <= recipients <= _MAX_RECIPIENTS
        or int(row[7]) < int(row[6])
    ):
        raise DraftStorageSchemaError("draft summary is invalid")
    in_reply_to = None
    if row[5] is not None:
        in_reply_to = _stored_text(
            row[5],
            "in_reply_to",
            max_characters=_MAX_IN_REPLY_TO,
            max_bytes=2_048,
            allow_body_controls=False,
            require_nonempty=True,
        )
    return EmailDraftSummary(
        draft_id=_stored_draft_id(row[0]),
        revision=_stored_revision(row[1]),
        state=state,
        subject=_stored_text(
            row[3],
            "subject",
            max_characters=_MAX_SUBJECT_CHARACTERS,
            max_bytes=_MAX_SUBJECT_BYTES,
            allow_body_controls=False,
        ),
        body_character_count=body_characters,
        in_reply_to=in_reply_to,
        created_at=_datetime(int(row[6])),
        updated_at=_datetime(int(row[7])),
        recipient_count=int(row[8]),
    )


def _datetime(timestamp: int) -> datetime:
    try:
        return datetime.fromtimestamp(timestamp / 1_000_000, UTC)
    except (OverflowError, OSError, ValueError):
        raise DraftStorageSchemaError("draft timestamp is invalid") from None


def _verify_private_owner(status: os.stat_result, *, is_directory: bool) -> None:
    if os.name == "nt":
        return
    expected_mode = 0o700 if is_directory else 0o600
    if status.st_uid != os.geteuid() or stat.S_IMODE(status.st_mode) != expected_mode:
        raise DraftStorageSecurityError("draft storage ownership or permissions are unsafe")


def _database_objects(connection: sqlite3.Connection) -> set[tuple[str, str]]:
    return {
        (str(row[0]), str(row[1]))
        for row in connection.execute(
            "SELECT type,name FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        ).fetchall()
    }


def _pragma_integer(connection: sqlite3.Connection, name: str) -> int:
    row = connection.execute(f"PRAGMA {name}").fetchone()
    if row is None or not isinstance(row[0], int):
        raise DraftStorageSchemaError("draft database metadata is invalid")
    return row[0]


def _raise_draft(error: DraftError) -> None:
    error.__cause__ = None
    error.__context__ = None
    raise error from None
