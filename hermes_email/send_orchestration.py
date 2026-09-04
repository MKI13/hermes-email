"""Durable exactly-once send orchestration for confirmed draft revisions."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from .sending import DraftSendCandidate
from .smtp import (
    SmtpDeliveryUnknownError,
    SmtpError,
    SmtpSubmission,
    SmtpTransport,
)

_DB_NAME: Final = "email-send-intents.sqlite3"
_SCHEMA_VERSION: Final = 2
_ALLOWED_STATES: Final = {"dispatching", "accepted", "definite-failure", "delivery-unknown"}
_PROCESS_TOKEN: Final = uuid.uuid4().hex
_PROCESS_LOCKS_GUARD = threading.Lock()
_PROCESS_LOCKS: dict[str, threading.RLock] = {}


class SendOrchestrationError(RuntimeError):
    """Base class for durable send orchestration failures."""


class InvalidSendOperationError(SendOrchestrationError):
    """Raised when a send operation ID or candidate is invalid."""


class SendOperationConflictError(SendOrchestrationError):
    """Raised when an operation ID is reused for different content."""


class DuplicateDraftSendError(SendOrchestrationError):
    """Raised when the same confirmed draft revision already has a send intent."""


@dataclass(frozen=True, slots=True)
class SendAttemptRecord:
    operation_id: str
    draft_id: str
    revision: int
    confirmation_id: str
    state: str
    replayed: bool

    @property
    def delivery_is_uncertain(self) -> bool:
        """True when SMTP acceptance cannot safely be determined."""
        return self.state in {"dispatching", "delivery-unknown"}

    @property
    def automatic_retry_forbidden(self) -> bool:
        """Persisted send intents are never eligible for automatic redispatch."""
        return True

    @property
    def manual_review_required(self) -> bool:
        """Unknown delivery requires an operator to verify external state."""
        return self.state == "delivery-unknown"


class SqliteSendIntentStore:
    """Profile-scoped durable send-intent ledger with fail-closed uniqueness."""

    def __init__(self, profile_data_dir: Path) -> None:
        self._path = Path(profile_data_dir) / _DB_NAME
        key = str(self._path.absolute())
        with _PROCESS_LOCKS_GUARD:
            self._process_lock = _PROCESS_LOCKS.setdefault(key, threading.RLock())

    @property
    def path(self) -> Path:
        return self._path

    def begin(self, operation_id: str, candidate: DraftSendCandidate) -> SendAttemptRecord:
        operation_id = _operation_id(operation_id)
        digest = _candidate_digest(candidate)
        with self._process_lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                self._recover_foreign_dispatches(connection)
                existing = connection.execute(
                    "SELECT operation_id, draft_id, revision, confirmation_id, request_digest, state "
                    "FROM send_intents WHERE operation_id = ?",
                    (operation_id,),
                ).fetchone()
                if existing is not None:
                    if existing[4] != digest:
                        raise SendOperationConflictError(
                            "send operation ID was reused with different content"
                        )
                    connection.commit()
                    return SendAttemptRecord(
                        existing[0], existing[1], existing[2], existing[3], existing[5], True
                    )

                duplicate = connection.execute(
                    "SELECT operation_id FROM send_intents WHERE draft_id = ? AND revision = ?",
                    (candidate.draft_id, candidate.revision),
                ).fetchone()
                if duplicate is not None:
                    raise DuplicateDraftSendError(
                        "this draft revision already has a durable send intent"
                    )

                now = _now()
                connection.execute(
                    "INSERT INTO send_intents "
                    "(operation_id, draft_id, revision, confirmation_id, request_digest, state, dispatcher_id, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, 'dispatching', ?, ?, ?)",
                    (
                        operation_id,
                        candidate.draft_id,
                        candidate.revision,
                        candidate.confirmation_id,
                        digest,
                        _PROCESS_TOKEN,
                        now,
                        now,
                    ),
                )
                connection.commit()
                return SendAttemptRecord(
                    operation_id,
                    candidate.draft_id,
                    candidate.revision,
                    candidate.confirmation_id,
                    "dispatching",
                    False,
                )
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    def finish(self, operation_id: str, state: str) -> SendAttemptRecord:
        operation_id = _operation_id(operation_id)
        if state not in _ALLOWED_STATES - {"dispatching"}:
            raise ValueError("invalid terminal send state")
        with self._process_lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT draft_id, revision, confirmation_id, state, dispatcher_id "
                    "FROM send_intents WHERE operation_id = ?",
                    (operation_id,),
                ).fetchone()
                if row is None:
                    raise InvalidSendOperationError("send operation does not exist")
                if row[3] != "dispatching":
                    connection.commit()
                    return SendAttemptRecord(
                        operation_id, row[0], row[1], row[2], row[3], True
                    )
                if row[4] != _PROCESS_TOKEN:
                    connection.execute(
                        "UPDATE send_intents SET state = 'delivery-unknown', dispatcher_id = NULL, updated_at = ? "
                        "WHERE operation_id = ? AND state = 'dispatching'",
                        (_now(), operation_id),
                    )
                    connection.commit()
                    return SendAttemptRecord(
                        operation_id, row[0], row[1], row[2], "delivery-unknown", True
                    )
                connection.execute(
                    "UPDATE send_intents SET state = ?, dispatcher_id = NULL, updated_at = ? "
                    "WHERE operation_id = ? AND state = 'dispatching' AND dispatcher_id = ?",
                    (state, _now(), operation_id, _PROCESS_TOKEN),
                )
                connection.commit()
                return SendAttemptRecord(operation_id, row[0], row[1], row[2], state, False)
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    def get(self, operation_id: str) -> SendAttemptRecord | None:
        operation_id = _operation_id(operation_id)
        with self._process_lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                self._recover_foreign_dispatches(connection)
                row = connection.execute(
                    "SELECT draft_id, revision, confirmation_id, state FROM send_intents WHERE operation_id = ?",
                    (operation_id,),
                ).fetchone()
                connection.commit()
                if row is None:
                    return None
                return SendAttemptRecord(operation_id, row[0], row[1], row[2], row[3], True)
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    def recover_interrupted_dispatches(self) -> int:
        """Convert dispatches owned by a prior process into delivery-unknown."""
        with self._process_lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                changed = self._recover_foreign_dispatches(connection)
                connection.commit()
                return changed
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    @staticmethod
    def _recover_foreign_dispatches(connection: sqlite3.Connection) -> int:
        cursor = connection.execute(
            "UPDATE send_intents SET state = 'delivery-unknown', dispatcher_id = NULL, updated_at = ? "
            "WHERE state = 'dispatching' AND (dispatcher_id IS NULL OR dispatcher_id != ?)",
            (_now(), _PROCESS_TOKEN),
        )
        return int(cursor.rowcount)

    def _connect(self) -> sqlite3.Connection:
        parent = self._path.parent
        parent.mkdir(parents=True, exist_ok=True)
        if self._path.is_symlink():
            raise SendOrchestrationError("send-intent database path must not be a symlink")
        if os.name == "posix":
            parent.chmod(0o700)
        connection = sqlite3.connect(self._path, timeout=5.0)
        if os.name == "posix":
            self._path.chmod(0o600)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = DELETE")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("PRAGMA trusted_schema = OFF")
        self._initialize_or_migrate(connection)
        return connection

    @staticmethod
    def _initialize_or_migrate(connection: sqlite3.Connection) -> None:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS meta (schema_version INTEGER NOT NULL CHECK(schema_version > 0))"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS send_intents ("
            "operation_id TEXT PRIMARY KEY,"
            "draft_id TEXT NOT NULL,"
            "revision INTEGER NOT NULL CHECK(revision > 0),"
            "confirmation_id TEXT NOT NULL,"
            "request_digest TEXT NOT NULL,"
            "state TEXT NOT NULL CHECK(state IN ('dispatching','accepted','definite-failure','delivery-unknown')),"
            "dispatcher_id TEXT,"
            "created_at TEXT NOT NULL,"
            "updated_at TEXT NOT NULL,"
            "UNIQUE(draft_id, revision)"
            ")"
        )
        count = connection.execute("SELECT COUNT(*) FROM meta").fetchone()[0]
        if count == 0:
            connection.execute("INSERT INTO meta(schema_version) VALUES (?)", (_SCHEMA_VERSION,))
            connection.commit()
            return
        if count != 1:
            connection.close()
            raise SendOrchestrationError("send-intent database schema is incompatible")
        version = connection.execute("SELECT schema_version FROM meta").fetchone()[0]
        if version == 1:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(send_intents)").fetchall()
            }
            if "dispatcher_id" not in columns:
                connection.execute("ALTER TABLE send_intents ADD COLUMN dispatcher_id TEXT")
            connection.execute("UPDATE meta SET schema_version = ?", (_SCHEMA_VERSION,))
            connection.commit()
            return
        if version != _SCHEMA_VERSION:
            connection.close()
            raise SendOrchestrationError("send-intent database schema is incompatible")


class IdempotentSendOrchestrator:
    """Persist intent before SMTP and never redispatch a persisted operation."""

    def __init__(self, store: SqliteSendIntentStore, transport: SmtpTransport) -> None:
        self._store = store
        self._transport = transport
        self._send_lock = threading.RLock()
        self._store.recover_interrupted_dispatches()

    def send_once(self, operation_id: str, candidate: DraftSendCandidate) -> SendAttemptRecord:
        with self._send_lock:
            record = self._store.begin(operation_id, candidate)
            if record.replayed:
                return record

            submission = SmtpSubmission(
                envelope_sender=candidate.envelope_sender,
                envelope_recipients=candidate.envelope_recipients,
                message_bytes=candidate.message_bytes,
                max_message_bytes=max(1_024, len(candidate.message_bytes)),
            )
            try:
                self._transport.submit_once(submission)
            except SmtpDeliveryUnknownError:
                return self._store.finish(operation_id, "delivery-unknown")
            except SmtpError:
                return self._store.finish(operation_id, "definite-failure")
            except BaseException:
                # The current process can no longer prove whether SMTP reached DATA.
                # Mark unknown before propagating whenever the process is still alive.
                self._store.finish(operation_id, "delivery-unknown")
                raise
            return self._store.finish(operation_id, "accepted")


def _operation_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or not 16 <= len(value) <= 128
        or not value.isascii()
        or any(character.isspace() for character in value)
    ):
        raise InvalidSendOperationError(
            "send_operation_id must be an opaque 16-to-128 character ASCII token"
        )
    return value


def _candidate_digest(candidate: DraftSendCandidate) -> str:
    if not isinstance(candidate, DraftSendCandidate):
        raise InvalidSendOperationError("send candidate is invalid")
    hasher = hashlib.sha256()
    parts = (
        candidate.draft_id,
        str(candidate.revision),
        candidate.account_namespace,
        candidate.envelope_sender,
        "\n".join(candidate.envelope_recipients),
        candidate.message_id,
        candidate.message_date.isoformat(),
        candidate.confirmation_id,
    )
    for part in parts:
        encoded = part.encode("utf-8")
        hasher.update(len(encoded).to_bytes(8, "big"))
        hasher.update(encoded)
    hasher.update(len(candidate.message_bytes).to_bytes(8, "big"))
    hasher.update(candidate.message_bytes)
    return hasher.hexdigest()


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()
