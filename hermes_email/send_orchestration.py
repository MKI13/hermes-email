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

from .send_files import FileLease, SendOrchestrationError, SendStorageError, SendBusyError
from .send_storage import SecureSendDatabase
from .sending import DraftSendCandidate, _message_id, _message_date
from .smtp import (
    SmtpDeliveryUnknownError,
    SmtpError,
    SmtpSubmission,
    SmtpSubmissionResult,
    SmtpTransport,
)

_DB_NAME: Final = "email-send-intents.sqlite3"
_SCHEMA_VERSION: Final = 4
_ALLOWED_STATES: Final = {"dispatching", "accepted", "definite-failure", "delivery-unknown"}


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
    message_id: str | None = None
    message_date: datetime | None = None

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


class SendOwnershipError(SendOrchestrationError):
    """Only the thread/process holding the dispatch lease may finish a live send."""


class SqliteSendIntentStore:
    """Non-pruning ledger with one kernel-backed dispatch owner per data directory.

    Short database locks and the long-lived dispatch lock are separate. Status
    reads therefore stay available while another process is inside SMTP.
    """

    def __init__(self, profile_data_dir: Path, *, max_database_bytes: int = 67_108_864,
                 max_intents: int = 100_000, legacy_workers_stopped: bool = False) -> None:
        self._path = Path(os.path.abspath(profile_data_dir)) / _DB_NAME
        self._database = SecureSendDatabase(self._path, max_database_bytes=max_database_bytes,
                                            max_intents=max_intents,legacy_workers_stopped=legacy_workers_stopped)
        self._mutex = threading.RLock()
        self._pid = os.getpid()
        self._lease: FileLease | None = None
        self._active_operation: str | None = None
        self._dispatcher: str | None = None
        self._owner_thread: int | None = None

    @property
    def path(self) -> Path:
        return self._path

    def _check_process(self) -> None:
        if self._pid != os.getpid():
            if self._lease is not None:
                self._lease.close_inherited()
            self._mutex = threading.RLock()
            self._lease = None
            self._active_operation = self._dispatcher = self._owner_thread = None
            self._pid = os.getpid()

    def close(self) -> None:
        self._check_process()
        with self._mutex:
            if self._lease is not None:
                raise SendBusyError("cannot close an active send owner")
            self._database.close()

    def _initialize(self) -> None:
        with self._database.transaction():
            pass

    @staticmethod
    def _record(operation_id: str, row: tuple, *, replayed: bool) -> SendAttemptRecord:
        return SendAttemptRecord(operation_id, row[0], row[1], row[2], row[3], replayed,
                                 row[4], datetime.fromisoformat(row[5]) if row[5] is not None else None)

    def begin(self, operation_id: str, candidate: DraftSendCandidate) -> SendAttemptRecord:
        self._check_process()
        operation_id = _operation_id(operation_id)
        digest = _candidate_digest(candidate)
        if candidate.send_operation_id is not None and candidate.send_operation_id != operation_id:
            raise InvalidSendOperationError("prepared operation identity does not match dispatch")
        with self._mutex:
            self._initialize()
            lease = FileLease(self._database.dispatch_path)
            acquired = self._lease is None and lease.acquire(create=False)
            retained = False
            try:
                with self._database.transaction() as connection:
                    if acquired:
                        lease.verify()
                        self._recover_unowned_dispatches(connection)
                    existing = connection.execute(
                        "SELECT draft_id,revision,confirmation_id,state,message_id,message_date,request_digest "
                        "FROM send_intents WHERE operation_id=?", (operation_id,),
                    ).fetchone()
                    if existing is not None:
                        if existing[6] != digest:
                            raise SendOperationConflictError("send operation ID was reused with different content")
                        return self._record(operation_id, existing[:6], replayed=True)
                    if connection.execute("SELECT 1 FROM send_intents WHERE draft_id=? AND revision=?",
                                          (candidate.draft_id,candidate.revision)).fetchone():
                        raise DuplicateDraftSendError("this draft revision already has a durable send intent")
                    if not acquired:
                        raise SendBusyError("another send owns this ledger; no new attempt was created")
                    if connection.execute("SELECT COUNT(*) FROM send_intents").fetchone()[0] >= self._database.max_intents:
                        raise SendStorageError("send intent capacity exhausted; no records pruned")
                    dispatcher = uuid.uuid4().hex
                    now = _now()
                    connection.execute(
                        "INSERT INTO send_intents (operation_id,draft_id,revision,confirmation_id,request_digest,"
                        "state,dispatcher_id,created_at,updated_at,message_id,message_date) "
                        "VALUES (?,?,?,?,?,'dispatching',?,?,?,?,?)",
                        (operation_id,candidate.draft_id,candidate.revision,candidate.confirmation_id,digest,
                         dispatcher,now,now,candidate.message_id,candidate.message_date.isoformat()),
                    )
                    lease.verify()
                self._lease = lease
                self._active_operation = operation_id
                self._dispatcher = dispatcher
                self._owner_thread = threading.get_ident()
                retained = True
                return SendAttemptRecord(operation_id,candidate.draft_id,candidate.revision,candidate.confirmation_id,
                                         "dispatching",False,candidate.message_id,candidate.message_date)
            finally:
                if acquired and not retained:
                    lease.close()

    def _require_owner(self, operation_id: str) -> None:
        if (self._lease is None or self._active_operation != operation_id
                or self._pid != os.getpid() or self._owner_thread != threading.get_ident()):
            raise SendOwnershipError("live dispatch is owned by another worker")
        self._lease.verify()

    def verify_dispatch(self, operation_id: str, candidate: DraftSendCandidate) -> None:
        self._check_process()
        with self._mutex:
            self._require_owner(operation_id)
            with self._database.transaction() as connection:
                row = connection.execute(
                    "SELECT request_digest,state,dispatcher_id,message_id,message_date FROM send_intents WHERE operation_id=?",
                    (_operation_id(operation_id),)).fetchone()
                if row != (_candidate_digest(candidate), "dispatching", self._dispatcher,
                           candidate.message_id,candidate.message_date.isoformat()):
                    raise SendStorageError("durable dispatch ownership is unavailable")
                self._require_owner(operation_id)

    def finish(self, operation_id: str, state: str) -> SendAttemptRecord:
        self._check_process()
        operation_id = _operation_id(operation_id)
        if state not in _ALLOWED_STATES - {"dispatching"}:
            raise InvalidSendOperationError("invalid terminal send state")
        with self._mutex:
            own = self._active_operation == operation_id and self._owner_thread == threading.get_ident()
            try:
                with self._database.transaction() as connection:
                    row = connection.execute(
                        "SELECT draft_id,revision,confirmation_id,state,message_id,message_date,dispatcher_id "
                        "FROM send_intents WHERE operation_id=?", (operation_id,)).fetchone()
                    if row is None:
                        raise InvalidSendOperationError("send operation does not exist")
                    if row[3] != "dispatching":
                        return self._record(operation_id,row[:6],replayed=True)
                    self._require_owner(operation_id)
                    if row[6] != self._dispatcher:
                        raise SendOwnershipError("durable send owner does not match")
                    connection.execute("UPDATE send_intents SET state=?,dispatcher_id=NULL,updated_at=? WHERE operation_id=?",
                                       (state,_now(),operation_id))
                    self._require_owner(operation_id)
                    return self._record(operation_id,(*row[:3],state,*row[4:6]),replayed=False)
            finally:
                if own:
                    self._release_dispatch(operation_id)

    def _release_dispatch(self, operation_id: str) -> None:
        """Called only after synchronous transport/finish has returned or raised."""
        self._check_process()
        with self._mutex:
            if self._lease is not None and self._active_operation == operation_id:
                if self._owner_thread != threading.get_ident():
                    raise SendOwnershipError("another worker still owns the send lease")
                self._lease.close()
                self._lease = None
                self._active_operation = self._dispatcher = self._owner_thread = None

    def get(self, operation_id: str) -> SendAttemptRecord | None:
        self._check_process()
        operation_id = _operation_id(operation_id)
        self.recover_interrupted_dispatches()
        with self._database.transaction() as connection:
            row = connection.execute(
                "SELECT draft_id,revision,confirmation_id,state,message_id,message_date FROM send_intents WHERE operation_id=?",
                (operation_id,)).fetchone()
            return None if row is None else self._record(operation_id,row,replayed=True)

    def recover_interrupted_dispatches(self) -> int:
        """Recover only with an exclusive OS lease, never by PID or elapsed time."""
        self._check_process()
        with self._mutex:
            self._initialize()
            if self._lease is not None:
                self._lease.verify()
                return 0
            lease = FileLease(self._database.dispatch_path)
            if not lease.acquire(create=False):
                return 0
            try:
                with self._database.transaction() as connection:
                    lease.verify()
                    count = self._recover_unowned_dispatches(connection)
                    lease.verify()
                    return count
            finally:
                lease.close()

    @staticmethod
    def _recover_unowned_dispatches(connection: sqlite3.Connection) -> int:
        return connection.execute(
            "UPDATE send_intents SET state='delivery-unknown',dispatcher_id=NULL,updated_at=? WHERE state='dispatching'",
            (_now(),),
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
            _candidate_digest(candidate)
            submission = SmtpSubmission(candidate.envelope_sender,candidate.envelope_recipients,
                                        candidate.message_bytes,max(1_024,len(candidate.message_bytes)))
            record = self._store.begin(operation_id,candidate)
            if record.replayed:
                return record
            try:
                self._store.verify_dispatch(operation_id,candidate)
                try:
                    result = self._transport.submit_once(submission)
                except SmtpDeliveryUnknownError:
                    state = "delivery-unknown"
                except SmtpError:
                    state = "definite-failure"
                except BaseException:
                    try:
                        self._store.finish(operation_id,"delivery-unknown")
                    except SendOrchestrationError:
                        # Durable dispatching also prevents retry if finish fails.
                        pass
                    raise
                else:
                    state = "accepted" if isinstance(result,SmtpSubmissionResult) and result.accepted is True else "delivery-unknown"
                return self._store.finish(operation_id,state)
            finally:
                self._store._release_dispatch(operation_id)


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
    _message_id(candidate.message_id)
    _message_date(candidate.message_date)
    if not isinstance(candidate.envelope_recipients, tuple):
        raise InvalidSendOperationError("send recipient snapshot must be immutable")
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
