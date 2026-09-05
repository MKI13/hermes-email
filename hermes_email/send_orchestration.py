"""Durable at-most-once SMTP attempts for confirmed draft revisions."""

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

from .send_files import SendOrchestrationError, SendStorageError
from .send_storage import SecureSendDatabase
from .sending import DraftSendCandidate
from .smtp import (
    SmtpDeliveryUnknownError,
    SmtpError,
    SmtpSubmission,
    SmtpTransport,
)

_DB_NAME: Final = "email-send-intents.sqlite3"
_SCHEMA_VERSION: Final = 3
_ALLOWED_STATES: Final = {"dispatching", "accepted", "definite-failure", "delivery-unknown"}
_PROCESS_TOKEN: Final = uuid.uuid4().hex
_PROCESS_LOCKS_GUARD = threading.Lock()
_PROCESS_LOCKS: dict[str, threading.RLock] = {}


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
    """Private non-pruning send ledger. Stop older workers before upgrading."""

    def __init__(self, profile_data_dir: Path, *, max_database_bytes: int = 67_108_864,
                 max_intents: int = 100_000) -> None:
        self._path = Path(os.path.abspath(profile_data_dir)) / _DB_NAME
        self._database = SecureSendDatabase(self._path, max_database_bytes=max_database_bytes,
                                            max_intents=max_intents)
        self._process_lock = threading.RLock()

    @property
    def path(self) -> Path:
        return self._path

    def close(self) -> None:
        self._database.close()

    def begin(self, operation_id: str, candidate: DraftSendCandidate) -> SendAttemptRecord:
        operation_id = _operation_id(operation_id)
        digest = _candidate_digest(candidate)
        with self._database.transaction() as connection:
            self._recover_foreign_dispatches(connection)
            existing = connection.execute(
                "SELECT operation_id,draft_id,revision,confirmation_id,request_digest,state "
                "FROM send_intents WHERE operation_id=?", (operation_id,),
            ).fetchone()
            if existing is not None:
                if existing[4] != digest:
                    raise SendOperationConflictError("send operation ID was reused with different content")
                return SendAttemptRecord(existing[0],existing[1],existing[2],existing[3],existing[5],True)
            if connection.execute("SELECT 1 FROM send_intents WHERE draft_id=? AND revision=?",
                                  (candidate.draft_id,candidate.revision)).fetchone():
                raise DuplicateDraftSendError("this draft revision already has a durable send intent")
            if connection.execute("SELECT COUNT(*) FROM send_intents").fetchone()[0] >= self._database.max_intents:
                raise SendStorageError("send intent capacity exhausted; no records pruned")
            now = _now()
            connection.execute(
                "INSERT INTO send_intents (operation_id,draft_id,revision,confirmation_id,request_digest,"
                "state,dispatcher_id,created_at,updated_at) VALUES (?,?,?,?,?,'dispatching',?,?,?)",
                (operation_id,candidate.draft_id,candidate.revision,candidate.confirmation_id,digest,_PROCESS_TOKEN,now,now),
            )
            return SendAttemptRecord(operation_id,candidate.draft_id,candidate.revision,candidate.confirmation_id,"dispatching",False)

    def verify_dispatch(self, operation_id: str, candidate: DraftSendCandidate) -> None:
        """Revalidate durable intent and file identity immediately before SMTP."""
        with self._database.transaction() as connection:
            row = connection.execute("SELECT request_digest,state,dispatcher_id FROM send_intents WHERE operation_id=?",
                                     (_operation_id(operation_id),)).fetchone()
            if row != (_candidate_digest(candidate), "dispatching", _PROCESS_TOKEN):
                raise SendStorageError("durable dispatch ownership is unavailable")

    def finish(self, operation_id: str, state: str) -> SendAttemptRecord:
        operation_id = _operation_id(operation_id)
        if state not in _ALLOWED_STATES - {"dispatching"}:
            raise InvalidSendOperationError("invalid terminal send state")
        with self._database.transaction() as connection:
            row = connection.execute("SELECT draft_id,revision,confirmation_id,state,dispatcher_id FROM send_intents WHERE operation_id=?",
                                     (operation_id,)).fetchone()
            if row is None:
                raise InvalidSendOperationError("send operation does not exist")
            if row[3] != "dispatching":
                return SendAttemptRecord(operation_id,row[0],row[1],row[2],row[3],True)
            if row[4] != _PROCESS_TOKEN:
                state = "delivery-unknown"
            connection.execute("UPDATE send_intents SET state=?,dispatcher_id=NULL,updated_at=? WHERE operation_id=?",
                               (state,_now(),operation_id))
            return SendAttemptRecord(operation_id,row[0],row[1],row[2],state,False)

    def get(self, operation_id: str) -> SendAttemptRecord | None:
        operation_id = _operation_id(operation_id)
        with self._database.transaction() as connection:
            self._recover_foreign_dispatches(connection)
            row = connection.execute("SELECT draft_id,revision,confirmation_id,state FROM send_intents WHERE operation_id=?",
                                     (operation_id,)).fetchone()
            return None if row is None else SendAttemptRecord(operation_id,row[0],row[1],row[2],row[3],True)

    def recover_interrupted_dispatches(self) -> int:
        with self._database.transaction() as connection:
            return self._recover_foreign_dispatches(connection)

    @staticmethod
    def _recover_foreign_dispatches(connection: sqlite3.Connection) -> int:
        return connection.execute(
            "UPDATE send_intents SET state='delivery-unknown',dispatcher_id=NULL,updated_at=? "
            "WHERE state='dispatching' AND (dispatcher_id IS NULL OR dispatcher_id != ?)",
            (_now(),_PROCESS_TOKEN),
        ).rowcount


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
                self._store.verify_dispatch(operation_id, candidate)
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
        or any(not 33 <= ord(character) <= 126 for character in value)
    ):
        raise InvalidSendOperationError(
            "send_operation_id must be an opaque 16-to-128 character ASCII token"
        )
    return value


def _candidate_digest(candidate: DraftSendCandidate) -> str:
    if not isinstance(candidate, DraftSendCandidate):
        raise InvalidSendOperationError("send candidate is invalid")
    _operation_id(candidate.confirmation_id)
    if (type(candidate.revision) is not int or not 1 <= candidate.revision <= 2_147_483_647
            or not isinstance(candidate.draft_id, str) or not 1 <= len(candidate.draft_id) <= 512
            or not candidate.draft_id.isascii() or any(not 33 <= ord(c) <= 126 for c in candidate.draft_id)
            or not isinstance(candidate.message_date, datetime) or candidate.message_date.tzinfo is None):
        raise InvalidSendOperationError("send candidate identity is invalid")
    SmtpSubmission(candidate.envelope_sender, candidate.envelope_recipients,
                   candidate.message_bytes, 10_000_000)
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
