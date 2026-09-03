import errno
import os
import sqlite3
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from hermes_email.config import EmailPluginConfig, StorageSettings
from hermes_email.models import EmailAddress, EmailMessage
from hermes_email.storage import (
    EmailStorageClosedError,
    EmailStorageResourceError,
    EmailStorageSchemaError,
    EmailStorageSecurityError,
    EmailStorageUnavailableError,
    EmailStorageValidationError,
    ObservationNamespace,
    SqliteObservationStore,
    observation_namespace,
)


def settings(**overrides) -> StorageSettings:
    values = {
        "mode": "sqlite",
        "account_namespace": "primary-inbox",
        "retention_days": 90,
        "max_observations": 10_000,
        "max_database_bytes": 16_777_216,
    }
    values.update(overrides)
    return StorageSettings(**values)


def namespace(
    account_namespace: str = "primary-inbox",
    *,
    provider: str = "mock",
    mailbox_key: str = "0" * 64,
) -> ObservationNamespace:
    return ObservationNamespace(account_namespace, provider, mailbox_key)


def message(message_id: str, *, private_marker: str = "PRIVATE BODY") -> EmailMessage:
    return EmailMessage(
        message_id=message_id,
        subject=f"PRIVATE SUBJECT {private_marker}",
        sender=EmailAddress("private-sender@example.invalid", "Private Sender"),
        recipients=(EmailAddress("private-recipient@example.invalid"),),
        body_text=private_marker,
        metadata={
            "rfc_message_id": "<attacker-controlled@example.invalid>",
            "host": "private-mail.example.invalid",
        },
    )


def make_store(
    tmp_path: Path,
    *,
    store_settings: StorageSettings | None = None,
    store_namespace: ObservationNamespace | None = None,
    clock=lambda: 1_000_000.0,
) -> SqliteObservationStore:
    return SqliteObservationStore(
        tmp_path / "data" / "email-observations.sqlite3",
        store_namespace or namespace(),
        store_settings or settings(),
        clock=clock,
    )


def test_fresh_store_creates_private_schema_and_deduplicates_exact_identity(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path)

    first = store.observe_messages((message("provider-id-1"),))
    second = store.observe_messages((message("provider-id-1"),))

    assert first.inserted == 1
    assert first.duplicates == 0
    assert second.inserted == 0
    assert second.duplicates == 1
    assert store.count_observations() == 1
    assert store.observation_count("provider-id-1") == 2
    database = store.path
    assert stat.S_IMODE(database.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(database.stat().st_mode) == 0o600

    connection = sqlite3.connect(database)
    try:
        assert connection.execute("PRAGMA user_version").fetchone() == (1,)
        assert connection.execute("PRAGMA application_id").fetchone() == (0x48454D4C,)
        assert connection.execute("PRAGMA quick_check").fetchone() == ("ok",)
        columns = [
            row[1] for row in connection.execute("PRAGMA table_info(observations)")
        ]
        assert columns == [
            "account_namespace",
            "provider",
            "mailbox_key",
            "message_id",
            "first_seen_at",
            "last_seen_at",
            "observation_count",
        ]
    finally:
        connection.close()


def test_oversized_existing_database_is_rejected_before_integrity_scan(
    tmp_path: Path,
) -> None:
    path = tmp_path / "data" / "email-observations.sqlite3"
    creator = SqliteObservationStore(
        path,
        namespace(),
        settings(max_database_bytes=16_777_216, max_observations=100_000),
    )
    creator.count_observations()
    connection = sqlite3.connect(path)
    connection.executemany(
        "INSERT INTO observations VALUES (?,?,?,?,?,?,1)",
        (
            ("account-one", "imap", "a" * 64, f"large-{index:04d}-" + "x" * 480, 1, 1)
            for index in range(3000)
        ),
    )
    connection.commit()
    connection.close()
    assert path.stat().st_size > 1_048_576

    class OrderedStore(SqliteObservationStore):
        events: list[str] = []

        def _enforce_page_limit(self, database) -> None:
            self.events.append("page-limit")
            super()._enforce_page_limit(database)

        def _check_integrity(self, database) -> None:
            self.events.append("integrity")
            super()._check_integrity(database)

    reader = OrderedStore(
        path,
        namespace(),
        settings(max_database_bytes=1_048_576, max_observations=100_000),
    )

    with pytest.raises(EmailStorageResourceError):
        reader.count_observations()

    assert reader.events == ["page-limit"]


def test_database_byte_cap_blocks_growth_across_subsequent_connections(
    tmp_path: Path,
) -> None:
    store = SqliteObservationStore(
        tmp_path / "data" / "email-observations.sqlite3",
        namespace(),
        settings(max_database_bytes=1_048_576, max_observations=100_000),
    )
    blocked = False
    completed_batches = 0

    for batch in range(100):
        messages = tuple(
            message(f"bounded-{batch:03d}-{index:03d}-" + "x" * 480)
            for index in range(100)
        )
        try:
            store.observe_messages(messages)
        except EmailStorageResourceError:
            blocked = True
            break
        completed_batches += 1

    assert completed_batches > 1
    assert blocked is True
    assert store.path.stat().st_size <= 1_048_576


def test_database_page_cap_is_reapplied_on_every_connection(tmp_path: Path) -> None:
    class CountingStore(SqliteObservationStore):
        page_limit_checks = 0

        def _enforce_page_limit(self, connection) -> None:
            self.page_limit_checks += 1
            super()._enforce_page_limit(connection)

    store = CountingStore(
        tmp_path / "data" / "email-observations.sqlite3",
        namespace(),
        settings(),
    )

    store.observe_messages((message("first"),))
    store.count_observations()
    store.observation_count("first")

    assert store.page_limit_checks == 3


def test_database_contains_identities_but_no_message_content_or_provider_metadata(
    tmp_path: Path,
) -> None:
    marker = "SYNTHETIC-PRIVATE-CONTENT-7f46"
    store = make_store(tmp_path)
    store.observe_messages((message("provider-id-privacy", private_marker=marker),))

    database_bytes = store.path.read_bytes()

    assert b"provider-id-privacy" in database_bytes
    assert marker.encode() not in database_bytes
    assert b"PRIVATE SUBJECT" not in database_bytes
    assert b"private-sender" not in database_bytes
    assert b"private-recipient" not in database_bytes
    assert b"attacker-controlled" not in database_bytes
    assert b"private-mail" not in database_bytes
    assert not (store.path.parent / f"{store.path.name}-wal").exists()
    assert not (store.path.parent / f"{store.path.name}-shm").exists()


def test_imap_namespace_persists_no_host_mailbox_or_credential_reference(
    tmp_path: Path,
) -> None:
    config = EmailPluginConfig.from_mapping(
        {
            "email": {"provider": "imap", "read_mode": "readonly"},
            "imap": {
                "host": "private-mail.example.invalid",
                "username_ref": "HERMES_EMAIL_PRIVATE_USERNAME",
                "password_ref": "HERMES_EMAIL_PRIVATE_PASSWORD",
                "mailbox": "Private Folder",
            },
            "storage": {
                "mode": "sqlite",
                "account_namespace": "account-one",
            },
        }
    )
    store = SqliteObservationStore(
        tmp_path / "data" / "email-observations.sqlite3",
        observation_namespace(config),
        config.storage,
    )

    store.observe_messages((message("imap-v1:100:5"),))
    database_bytes = store.path.read_bytes()

    assert b"private-mail" not in database_bytes
    assert b"Private Folder" not in database_bytes
    assert b"HERMES_EMAIL_PRIVATE" not in database_bytes


def test_same_provider_id_is_distinct_across_account_and_mailbox_namespaces(
    tmp_path: Path,
) -> None:
    path = tmp_path / "data" / "email-observations.sqlite3"
    first = SqliteObservationStore(path, namespace("account-a"), settings())
    second = SqliteObservationStore(path, namespace("account-b"), settings())
    third = SqliteObservationStore(
        path,
        namespace("account-a", mailbox_key="1" * 64),
        settings(),
    )

    assert first.observe_messages((message("same-id"),)).inserted == 1
    assert second.observe_messages((message("same-id"),)).inserted == 1
    assert third.observe_messages((message("same-id"),)).inserted == 1
    assert first.count_observations() == 3


def test_imap_uidvalidity_identity_is_not_replaced_by_rfc_message_id(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path)
    old_epoch = message("imap-v1:100:5")
    new_epoch = message("imap-v1:200:5")
    copied_uid = message("imap-v1:200:6")

    result = store.observe_messages((old_epoch, new_epoch, copied_uid))

    assert result.inserted == 3
    assert result.duplicates == 0
    assert store.count_observations() == 3


def test_backward_clock_never_regresses_last_seen_timestamp(tmp_path: Path) -> None:
    path = tmp_path / "data" / "email-observations.sqlite3"
    newer = SqliteObservationStore(path, namespace(), settings(), clock=lambda: 200)
    older = SqliteObservationStore(path, namespace(), settings(), clock=lambda: 100)

    newer.observe_messages((message("clock-safe"),))
    older.observe_messages((message("clock-safe"),))
    connection = sqlite3.connect(path)
    try:
        row = connection.execute(
            "SELECT first_seen_at,last_seen_at,observation_count "
            "FROM observations WHERE message_id='clock-safe'"
        ).fetchone()
    finally:
        connection.close()

    assert row == (200, 200, 2)


def test_retention_and_capacity_prune_only_during_explicit_write(tmp_path: Path) -> None:
    now = [1_000_000.0]
    store = make_store(
        tmp_path,
        store_settings=settings(retention_days=1, max_observations=2),
        clock=lambda: now[0],
    )
    store.observe_messages((message("old"),))
    now[0] += 86_401

    result = store.observe_messages((message("new-a"), message("new-b")))

    assert result.pruned == 1
    assert store.count_observations() == 2
    assert store.observation_count("old") == 0
    assert store.observation_count("new-a") == 1


def test_capacity_prunes_deterministically_at_equal_timestamp(tmp_path: Path) -> None:
    store = make_store(
        tmp_path,
        store_settings=settings(max_observations=2),
    )

    result = store.observe_messages(
        (message("message-a"), message("message-b"), message("message-c"))
    )

    assert result.pruned == 1
    assert store.count_observations() == 2


def test_concurrent_exact_observations_have_one_insert_winner(tmp_path: Path) -> None:
    path = tmp_path / "data" / "email-observations.sqlite3"

    def observe_once(_index: int):
        store = SqliteObservationStore(path, namespace(), settings())
        return store.observe_messages((message("raced-id"),))

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(observe_once, range(8)))

    assert sum(result.inserted for result in results) == 1
    assert sum(result.duplicates for result in results) == 7
    verifier = SqliteObservationStore(path, namespace(), settings())
    assert verifier.count_observations() == 1
    assert verifier.observation_count("raced-id") == 8


def test_transaction_rolls_back_when_pruning_fails(tmp_path: Path) -> None:
    class FailingPruneStore(SqliteObservationStore):
        def _prune(self, connection, now):
            del connection, now
            raise EmailStorageResourceError("synthetic bounded failure")

    path = tmp_path / "data" / "email-observations.sqlite3"
    store = FailingPruneStore(path, namespace(), settings())

    with pytest.raises(EmailStorageResourceError):
        store.observe_messages((message("rolled-back"),))

    verifier = SqliteObservationStore(path, namespace(), settings())
    assert verifier.count_observations() == 0


def test_page_limit_consumes_only_one_item_beyond_bound(tmp_path: Path) -> None:
    consumed = 0

    def messages():
        nonlocal consumed
        while True:
            consumed += 1
            yield message(f"bounded-{consumed}")

    store = make_store(tmp_path)
    with pytest.raises(EmailStorageResourceError):
        store.observe_messages(messages())

    assert consumed == 101
    assert store.path.exists() is False


def test_page_validation_happens_before_database_creation(tmp_path: Path) -> None:
    store = make_store(tmp_path)

    with pytest.raises(EmailStorageResourceError):
        store.observe_messages(tuple(message(f"id-{index}") for index in range(101)))

    assert not store.path.exists()


def test_identifier_validation_happens_before_database_creation(tmp_path: Path) -> None:
    store = make_store(tmp_path)

    with pytest.raises(EmailStorageValidationError):
        store.observe_messages((message("x" * 513),))

    assert not store.path.exists()


def test_close_is_idempotent_and_rejects_late_operations(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.observe_messages((message("before-close"),))

    store.close()
    store.close()

    with pytest.raises(EmailStorageClosedError):
        store.observe_messages((message("after-close"),))
    with pytest.raises(EmailStorageClosedError):
        store.count_observations()


def test_concurrent_file_creation_loser_reopens_winner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "data" / "email-observations.sqlite3"
    path.parent.mkdir(mode=0o700)
    real_open = os.open
    calls = 0

    def racing_open(target, flags, mode=0o777):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise FileNotFoundError
        if calls == 2:
            descriptor = real_open(target, flags, mode)
            os.close(descriptor)
            raise FileExistsError
        return real_open(target, flags, mode)

    monkeypatch.setattr(os, "open", racing_open)
    store = SqliteObservationStore(path, namespace(), settings())

    assert store.count_observations() == 0
    assert calls == 3


@pytest.mark.parametrize("error_number", [errno.ENOSPC, errno.EMFILE, errno.ENFILE])
def test_resource_path_failures_have_resource_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error_number: int
) -> None:
    path = tmp_path / "data" / "email-observations.sqlite3"
    path.parent.mkdir(mode=0o700)
    store = SqliteObservationStore(path, namespace(), settings())

    def fail_open(*args, **kwargs):
        del args, kwargs
        raise OSError(error_number, "private")

    monkeypatch.setattr(os, "open", fail_open)
    with pytest.raises(EmailStorageResourceError):
        store.count_observations()


def test_permission_path_failure_is_unavailable_not_insecure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "data" / "email-observations.sqlite3"
    path.parent.mkdir(mode=0o700)
    store = SqliteObservationStore(path, namespace(), settings())
    monkeypatch.setattr(
        os,
        "open",
        lambda *args, **kwargs: (_ for _ in ()).throw(PermissionError("private")),
    )

    with pytest.raises(EmailStorageUnavailableError):
        store.count_observations()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission enforcement")
def test_rejects_permissive_existing_directory_without_chmod(tmp_path: Path) -> None:
    directory = tmp_path / "data"
    directory.mkdir(mode=0o700)
    directory.chmod(0o755)
    store = SqliteObservationStore(
        directory / "email-observations.sqlite3", namespace(), settings()
    )

    with pytest.raises(EmailStorageSecurityError):
        store.count_observations()

    assert stat.S_IMODE(directory.stat().st_mode) == 0o755


def test_rejects_symlink_storage_directory(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    directory = tmp_path / "data"
    try:
        directory.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    store = SqliteObservationStore(
        directory / "email-observations.sqlite3", namespace(), settings()
    )

    with pytest.raises(EmailStorageSecurityError):
        store.count_observations()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission enforcement")
def test_rejects_permissive_existing_database_without_chmod(tmp_path: Path) -> None:
    database = tmp_path / "data" / "email-observations.sqlite3"
    database.parent.mkdir(mode=0o700)
    database.touch(mode=0o600)
    database.chmod(0o644)
    store = SqliteObservationStore(database, namespace(), settings())

    with pytest.raises(EmailStorageSecurityError):
        store.count_observations()

    assert stat.S_IMODE(database.stat().st_mode) == 0o644


def test_rejects_symlink_database(tmp_path: Path) -> None:
    directory = tmp_path / "data"
    directory.mkdir(mode=0o700)
    target = tmp_path / "target.sqlite3"
    target.touch(mode=0o600)
    database = directory / "email-observations.sqlite3"
    try:
        database.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    store = SqliteObservationStore(database, namespace(), settings())

    with pytest.raises(EmailStorageSecurityError):
        store.count_observations()


def test_rejects_hardlinked_database(tmp_path: Path) -> None:
    directory = tmp_path / "data"
    directory.mkdir(mode=0o700)
    target = directory / "target.sqlite3"
    target.touch(mode=0o600)
    database = directory / "email-observations.sqlite3"
    try:
        os.link(target, database)
    except OSError:
        pytest.skip("hardlink creation is unavailable")
    store = SqliteObservationStore(database, namespace(), settings())

    with pytest.raises(EmailStorageSecurityError):
        store.count_observations()


@pytest.mark.parametrize(
    ("application_id", "user_version"),
    [(0, 2), (123, 1), (123, 2)],
)
def test_rejects_incompatible_database_identity(
    tmp_path: Path, application_id: int, user_version: int
) -> None:
    database = tmp_path / "data" / "email-observations.sqlite3"
    database.parent.mkdir(mode=0o700)
    database.touch(mode=0o600)
    connection = sqlite3.connect(database)
    connection.execute(f"PRAGMA application_id={application_id}")
    connection.execute(f"PRAGMA user_version={user_version}")
    connection.close()
    store = SqliteObservationStore(database, namespace(), settings())

    with pytest.raises(EmailStorageSchemaError):
        store.count_observations()

    assert database.exists()


def test_revalidates_schema_on_every_new_connection(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.observe_messages((message("before-tamper"),))
    connection = sqlite3.connect(store.path)
    connection.execute("DROP INDEX observations_last_seen")
    connection.commit()
    connection.close()

    with pytest.raises(EmailStorageSchemaError):
        store.count_observations()


def test_rejects_foreign_wal_database_without_changing_mode_or_main_bytes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "data" / "email-observations.sqlite3"
    path.parent.mkdir(mode=0o700)
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE foreign_data(value TEXT)")
    connection.commit()
    assert connection.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
    connection.close()
    path.chmod(0o600)
    original_bytes = path.read_bytes()
    original_entries = {entry.name for entry in path.parent.iterdir()}
    store = SqliteObservationStore(path, namespace(), settings())

    with pytest.raises(EmailStorageSchemaError):
        store.count_observations()

    assert path.read_bytes() == original_bytes
    assert {entry.name for entry in path.parent.iterdir()} == original_entries
    verifier = sqlite3.connect(path)
    try:
        assert verifier.execute("PRAGMA journal_mode").fetchone() == ("wal",)
    finally:
        verifier.close()


def test_rejects_version_zero_database_with_existing_objects(tmp_path: Path) -> None:
    database = tmp_path / "data" / "email-observations.sqlite3"
    database.parent.mkdir(mode=0o700)
    database.touch(mode=0o600)
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE foreign_data(value TEXT)")
    connection.commit()
    connection.close()
    store = SqliteObservationStore(database, namespace(), settings())

    with pytest.raises(EmailStorageSchemaError):
        store.count_observations()


def test_rejects_truncated_database_without_recreating_it(tmp_path: Path) -> None:
    database = tmp_path / "data" / "email-observations.sqlite3"
    database.parent.mkdir(mode=0o700)
    database.write_bytes(b"not a sqlite database PRIVATE")
    database.chmod(0o600)
    before = database.read_bytes()
    store = SqliteObservationStore(database, namespace(), settings())

    with pytest.raises(EmailStorageSchemaError):
        store.count_observations()

    assert database.read_bytes() == before


def test_observation_namespace_uses_explicit_account_and_hashed_mailbox() -> None:
    config = EmailPluginConfig.from_mapping(
        {
            "email": {"provider": "imap", "read_mode": "readonly"},
            "imap": {
                "host": "mail.example.com",
                "username_ref": "HERMES_EMAIL_IMAP_USERNAME",
                "password_ref": "HERMES_EMAIL_IMAP_PASSWORD",
                "mailbox": "Private Folder",
            },
            "storage": {
                "mode": "sqlite",
                "account_namespace": "account-one",
            },
        }
    )

    identity = observation_namespace(config)

    assert identity.account_namespace == "account-one"
    assert identity.provider == "imap"
    assert identity.mailbox_key != "Private Folder"
    assert len(identity.mailbox_key) == 64
