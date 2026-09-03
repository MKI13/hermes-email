"""Private SQLite observation ledger for exact provider-message deduplication."""

from __future__ import annotations

import errno
import hashlib
import itertools
import os
import sqlite3
import stat
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Final, Iterable

from .config import EmailPluginConfig, StorageSettings
from .models import EmailMessage

_SCHEMA_VERSION: Final = 1
_APPLICATION_ID: Final = 0x48454D4C
_MAX_PAGE_MESSAGES: Final = 100
_MAX_IDENTIFIER_CHARACTERS: Final = 512
_MAX_OBSERVATION_COUNT: Final = 2_147_483_647
_SQLITE_TIMEOUT_SECONDS: Final = 5
_CREATE_TABLE_SQL: Final = (
    "CREATE TABLE observations ("
    "account_namespace TEXT NOT NULL,"
    "provider TEXT NOT NULL,"
    "mailbox_key TEXT NOT NULL,"
    "message_id TEXT NOT NULL,"
    "first_seen_at INTEGER NOT NULL,"
    "last_seen_at INTEGER NOT NULL,"
    "observation_count INTEGER NOT NULL "
    "CHECK(observation_count BETWEEN 1 AND 2147483647),"
    "CHECK(first_seen_at <= last_seen_at),"
    "PRIMARY KEY(account_namespace,provider,mailbox_key,message_id)"
    ") WITHOUT ROWID"
)
_CREATE_INDEX_SQL: Final = (
    "CREATE INDEX observations_last_seen ON observations(last_seen_at)"
)
_EXPECTED_COLUMNS: Final = (
    ("account_namespace", "TEXT", 1, 1),
    ("provider", "TEXT", 1, 2),
    ("mailbox_key", "TEXT", 1, 3),
    ("message_id", "TEXT", 1, 4),
    ("first_seen_at", "INTEGER", 1, 0),
    ("last_seen_at", "INTEGER", 1, 0),
    ("observation_count", "INTEGER", 1, 0),
)


class EmailStorageError(RuntimeError):
    """Base class for fixed, non-sensitive persistence failures."""


class EmailStorageUnavailableError(EmailStorageError):
    """Raised when the local database cannot complete a bounded operation."""


class EmailStorageClosedError(EmailStorageUnavailableError):
    """Raised when persistence is attempted after plugin unload."""


class EmailStorageSecurityError(EmailStorageError):
    """Raised when the database path is not a private regular file."""


class EmailStorageSchemaError(EmailStorageError):
    """Raised when the database identity, schema, or integrity is invalid."""


class EmailStorageResourceError(EmailStorageError):
    """Raised when a configured persistence resource limit is exceeded."""


class EmailStorageValidationError(EmailStorageError):
    """Raised when a provider identity cannot be persisted safely."""


@dataclass(frozen=True, slots=True)
class ObservationNamespace:
    """Stable non-secret identity for one configured provider mailbox."""

    account_namespace: str
    provider: str
    mailbox_key: str


@dataclass(frozen=True, slots=True)
class StoreResult:
    """Counts from one atomic observation-ledger operation."""

    inserted: int
    duplicates: int
    pruned: int


class SqliteObservationStore:
    """Persist exact provider-message observations without message content."""

    def __init__(
        self,
        path: str | Path,
        namespace: ObservationNamespace,
        settings: StorageSettings,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = Path(path)
        self.namespace = namespace
        self.settings = settings
        self._clock = clock
        self._lock = threading.RLock()
        self._closed = False
        self._initialized = False

    def close(self) -> None:
        """Wait for the active operation and reject every later operation."""
        with self._lock:
            self._closed = True

    def observe_messages(self, messages: Iterable[EmailMessage]) -> StoreResult:
        """Atomically record one bounded page using exact provider identities."""
        with self._lock:
            self._ensure_open()
            message_ids = tuple(
                _message_id(message)
                for message in itertools.islice(messages, _MAX_PAGE_MESSAGES + 1)
            )
            if len(message_ids) > _MAX_PAGE_MESSAGES:
                raise EmailStorageResourceError("email observation page limit exceeded")
            now = int(self._clock())
            connection = self._connect()
            try:
                self._initialize(connection)
                connection.execute("BEGIN IMMEDIATE")
                inserted = 0
                duplicates = 0
                for message_id in message_ids:
                    cursor = connection.execute(
                        "INSERT OR IGNORE INTO observations VALUES (?,?,?,?,?,?,1)",
                        (
                            self.namespace.account_namespace,
                            self.namespace.provider,
                            self.namespace.mailbox_key,
                            message_id,
                            now,
                            now,
                        ),
                    )
                    if cursor.rowcount == 1:
                        inserted += 1
                    else:
                        connection.execute(
                            "UPDATE observations SET last_seen_at=MAX(last_seen_at,?), "
                            "observation_count=MIN(observation_count+1,?) "
                            "WHERE account_namespace=? AND provider=? "
                            "AND mailbox_key=? AND message_id=?",
                            (
                                now,
                                _MAX_OBSERVATION_COUNT,
                                self.namespace.account_namespace,
                                self.namespace.provider,
                                self.namespace.mailbox_key,
                                message_id,
                            ),
                        )
                        duplicates += 1
                pruned = self._prune(connection, now)
                connection.commit()
                return StoreResult(inserted, duplicates, pruned)
            except EmailStorageError:
                connection.rollback()
                raise
            except sqlite3.DatabaseError as error:
                connection.rollback()
                self._raise_database_error(error)
            finally:
                connection.close()

    def count_observations(self) -> int:
        """Return the stored identity count without exposing any identity."""
        with self._lock:
            self._ensure_open()
            connection = self._connect()
            try:
                self._initialize(connection)
                row = connection.execute("SELECT COUNT(*) FROM observations").fetchone()
                if row is None or not isinstance(row[0], int):
                    raise EmailStorageSchemaError("email observation count is invalid")
                return row[0]
            except EmailStorageError:
                raise
            except sqlite3.DatabaseError as error:
                self._raise_database_error(error)
            finally:
                connection.close()

    def observation_count(self, message_id: str) -> int:
        """Return how often one exact provider identity has been observed."""
        _validate_identifier(message_id)
        with self._lock:
            self._ensure_open()
            connection = self._connect()
            try:
                self._initialize(connection)
                row = connection.execute(
                    "SELECT observation_count FROM observations "
                    "WHERE account_namespace=? AND provider=? "
                    "AND mailbox_key=? AND message_id=?",
                    (
                        self.namespace.account_namespace,
                        self.namespace.provider,
                        self.namespace.mailbox_key,
                        message_id,
                    ),
                ).fetchone()
                return 0 if row is None else int(row[0])
            except EmailStorageError:
                raise
            except (TypeError, ValueError, sqlite3.DatabaseError) as error:
                if isinstance(error, sqlite3.DatabaseError):
                    self._raise_database_error(error)
                raise EmailStorageSchemaError("email observation count is invalid") from None
            finally:
                connection.close()

    def _ensure_open(self) -> None:
        if self._closed:
            raise EmailStorageClosedError("email observation storage is closed")

    def _connect(self) -> sqlite3.Connection:
        expected_identity = self._prepare_private_path()
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                self.path,
                timeout=_SQLITE_TIMEOUT_SECONDS,
                isolation_level=None,
            )
            current_status = self.path.lstat()
            if (
                self.path.is_symlink()
                or not stat.S_ISREG(current_status.st_mode)
                or current_status.st_nlink != 1
                or (current_status.st_dev, current_status.st_ino) != expected_identity
            ):
                raise EmailStorageSecurityError(
                    "email observation database identity changed"
                )
            connection.execute(f"PRAGMA busy_timeout={_SQLITE_TIMEOUT_SECONDS * 1000}")
            return connection
        except EmailStorageError:
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
            _raise_storage(
                EmailStorageUnavailableError("email observation path is unavailable")
            )

    def _prepare_private_path(self) -> tuple[int, int]:
        parent = self.path.parent
        try:
            parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            parent_status = parent.lstat()
            if not stat.S_ISDIR(parent_status.st_mode) or parent.is_symlink():
                raise EmailStorageSecurityError(
                    "email observation directory is not private"
                )
            _verify_private_owner(parent_status, is_directory=True)
            flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
            if self.path.is_symlink():
                raise EmailStorageSecurityError(
                    "email observation database is not a private regular file"
                )
            try:
                descriptor = os.open(self.path, flags)
                created = False
            except FileNotFoundError:
                try:
                    descriptor = os.open(
                        self.path,
                        flags | os.O_CREAT | os.O_EXCL,
                        0o600,
                    )
                    created = True
                except FileExistsError:
                    descriptor = os.open(self.path, flags)
                    created = False
            try:
                file_status = os.fstat(descriptor)
                if not stat.S_ISREG(file_status.st_mode) or file_status.st_nlink != 1:
                    raise EmailStorageSecurityError(
                        "email observation database is not a private regular file"
                    )
                _verify_private_owner(file_status, is_directory=False)
                if created and os.name != "nt" and stat.S_IMODE(file_status.st_mode) != 0o600:
                    raise EmailStorageSecurityError(
                        "email observation database permissions are unsafe"
                    )
            finally:
                os.close(descriptor)
            return (file_status.st_dev, file_status.st_ino)
        except EmailStorageError:
            raise
        except OSError as error:
            if error.errno in {errno.ELOOP}:
                _raise_storage(
                    EmailStorageSecurityError("email observation path is unsafe")
                )
            if error.errno in {
                errno.ENOSPC,
                errno.EMFILE,
                errno.ENFILE,
                getattr(errno, "EDQUOT", errno.ENOSPC),
            }:
                _raise_storage(
                    EmailStorageResourceError("email observation storage resource unavailable")
                )
            _raise_storage(
                EmailStorageUnavailableError("email observation path is unavailable")
            )

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
                connection.execute(_CREATE_TABLE_SQL)
                connection.execute(_CREATE_INDEX_SQL)
                connection.execute(f"PRAGMA application_id={_APPLICATION_ID}")
                connection.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
            elif version != _SCHEMA_VERSION or application_id != _APPLICATION_ID:
                raise EmailStorageSchemaError(
                    "email observation database identity is incompatible"
                )
            self._verify_database(connection)
            connection.commit()
            self._configure_connection(connection)
            self._initialized = True
        except EmailStorageError:
            connection.rollback()
            raise
        except sqlite3.DatabaseError as error:
            connection.rollback()
            self._raise_database_error(error)

    def _verify_database(self, connection: sqlite3.Connection) -> None:
        if _pragma_integer(connection, "application_id") != _APPLICATION_ID:
            raise EmailStorageSchemaError(
                "email observation database identity is incompatible"
            )
        if _pragma_integer(connection, "user_version") != _SCHEMA_VERSION:
            raise EmailStorageSchemaError(
                "email observation database version is incompatible"
            )
        if _database_objects(connection) != {
            ("index", "observations_last_seen"),
            ("table", "observations"),
        }:
            raise EmailStorageSchemaError("email observation database schema is invalid")
        columns = connection.execute("PRAGMA table_info(observations)").fetchall()
        column_contract = tuple((row[1], row[2], row[3], row[5]) for row in columns)
        if column_contract != _EXPECTED_COLUMNS:
            raise EmailStorageSchemaError("email observation database schema is invalid")
        definitions = dict(
            connection.execute(
                "SELECT name,sql FROM sqlite_master "
                "WHERE name IN ('observations','observations_last_seen')"
            ).fetchall()
        )
        if definitions != {
            "observations": _CREATE_TABLE_SQL,
            "observations_last_seen": _CREATE_INDEX_SQL,
        }:
            raise EmailStorageSchemaError("email observation database schema is invalid")
        self._enforce_page_limit(connection)
        self._check_integrity(connection)

    def _check_integrity(self, connection: sqlite3.Connection) -> None:
        integrity = connection.execute("PRAGMA quick_check(1)").fetchone()
        if integrity != ("ok",):
            raise EmailStorageSchemaError("email observation database integrity failed")

    def _configure_connection(self, connection: sqlite3.Connection) -> None:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("PRAGMA secure_delete=ON")
        connection.execute("PRAGMA temp_store=MEMORY")
        journal = connection.execute("PRAGMA journal_mode=DELETE").fetchone()
        if journal is None or str(journal[0]).casefold() != "delete":
            raise EmailStorageUnavailableError(
                "email observation journal mode is unavailable"
            )
        connection.execute("PRAGMA synchronous=FULL")
        if (
            _pragma_integer(connection, "foreign_keys") != 1
            or _pragma_integer(connection, "trusted_schema") != 0
            or _pragma_integer(connection, "secure_delete") != 1
            or _pragma_integer(connection, "temp_store") != 2
            or _pragma_integer(connection, "synchronous") != 2
        ):
            raise EmailStorageUnavailableError(
                "email observation safety settings are unavailable"
            )

    def _enforce_page_limit(self, connection: sqlite3.Connection) -> None:
        page_size = _pragma_integer(connection, "page_size")
        page_count = _pragma_integer(connection, "page_count")
        if page_size <= 0:
            raise EmailStorageSchemaError("email observation page metadata is invalid")
        maximum_pages = max(1, self.settings.max_database_bytes // page_size)
        if page_count > maximum_pages:
            raise EmailStorageResourceError(
                "email observation database exceeds its size limit"
            )
        result = connection.execute(f"PRAGMA max_page_count={maximum_pages}").fetchone()
        if result != (maximum_pages,):
            raise EmailStorageResourceError(
                "email observation database size limit is unavailable"
            )

    def _prune(self, connection: sqlite3.Connection, now: int) -> int:
        cutoff = now - self.settings.retention_days * 86_400
        expired = connection.execute(
            "DELETE FROM observations WHERE last_seen_at < ?", (cutoff,)
        ).rowcount
        excess = connection.execute(
            "DELETE FROM observations WHERE (account_namespace,provider,mailbox_key,message_id) "
            "IN (SELECT account_namespace,provider,mailbox_key,message_id "
            "FROM observations ORDER BY last_seen_at DESC,account_namespace DESC,"
            "provider DESC,mailbox_key DESC,message_id DESC LIMIT -1 OFFSET ?)",
            (self.settings.max_observations,),
        ).rowcount
        return max(0, expired) + max(0, excess)

    @staticmethod
    def _raise_database_error(error: sqlite3.DatabaseError) -> None:
        error_code = getattr(error, "sqlite_errorcode", None)
        if error_code == sqlite3.SQLITE_FULL:
            _raise_storage(
                EmailStorageResourceError("email observation database is full")
            )
        if error_code in {sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB}:
            _raise_storage(
                EmailStorageSchemaError("email observation database is corrupt")
            )
        _raise_storage(
            EmailStorageUnavailableError("email observation operation failed")
        )


def observation_namespace(config: EmailPluginConfig) -> ObservationNamespace:
    """Build one stable namespace without retaining host or credential references."""
    account_namespace = config.storage.account_namespace
    if config.storage.mode != "sqlite" or account_namespace is None:
        raise EmailStorageValidationError("email observation namespace is unavailable")
    provider = (config.email.provider or "").strip().casefold()
    mailbox = config.imap.mailbox if provider == "imap" else "default"
    mailbox_key = hashlib.sha256(mailbox.encode("utf-8")).hexdigest()
    return ObservationNamespace(account_namespace, provider, mailbox_key)


def _message_id(message: EmailMessage) -> str:
    _validate_identifier(message.message_id)
    return message.message_id


def _validate_identifier(value: object) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > _MAX_IDENTIFIER_CHARACTERS
    ):
        raise EmailStorageValidationError("email observation identifier is invalid")


def _verify_private_owner(file_status: os.stat_result, *, is_directory: bool) -> None:
    if os.name == "nt":
        return
    if file_status.st_uid != os.geteuid():
        raise EmailStorageSecurityError("email observation path owner is unsafe")
    expected = 0o700 if is_directory else 0o600
    if stat.S_IMODE(file_status.st_mode) != expected:
        raise EmailStorageSecurityError("email observation path permissions are unsafe")


def _pragma_integer(connection: sqlite3.Connection, name: str) -> int:
    row = connection.execute(f"PRAGMA {name}").fetchone()
    if row is None or not isinstance(row[0], int):
        raise EmailStorageSchemaError("email observation database metadata is invalid")
    return row[0]


def _database_objects(connection: sqlite3.Connection) -> set[tuple[str, str]]:
    rows = connection.execute(
        "SELECT type,name FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
    ).fetchall()
    if any(
        not isinstance(object_type, str) or not isinstance(name, str)
        for object_type, name in rows
    ):
        raise EmailStorageSchemaError("email observation database schema is invalid")
    return {(object_type, name) for object_type, name in rows}


def _raise_storage(error: EmailStorageError) -> None:
    error.__cause__ = None
    error.__context__ = None
    raise error from None
