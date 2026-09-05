"""Regression coverage for private audit paths, lifecycle and transactions."""

import os
import sqlite3
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from hermes_email.audit import (
    AuditError, ContentMinimizedAuditStore, _TABLE_SQL, _INDEX_SQL,
    _APPLICATION_ID, _ALLOWED_OPERATIONS,
)
from hermes_email.config import AuditSettings


def store_at(tmp_path: Path, **options) -> ContentMinimizedAuditStore:
    return ContentMinimizedAuditStore(
        tmp_path / "private" / "audit.sqlite3", AuditSettings(mode="sqlite", **options)
    )


def test_first_write_is_private_even_with_permissive_umask(tmp_path):
    store = store_at(tmp_path)
    previous = os.umask(0)
    try:
        store.record("list", "ok", 2)
    finally:
        os.umask(previous)
    if os.name == "posix":
        assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
        assert stat.S_IMODE(store.path.parent.stat().st_mode) == 0o700


def test_fresh_recent_is_non_creating_and_closed_store_rejects_both(tmp_path):
    store = store_at(tmp_path)
    assert store.recent() == []
    assert not store.path.parent.exists()
    store.record("get", "ok", 1)
    before = store.path.read_bytes()
    store.close()
    for operation in (lambda: store.recent(), lambda: store.record("get", "ok", 1)):
        with pytest.raises(AuditError, match="closed"):
            operation()
    assert store.path.read_bytes() == before


@pytest.mark.parametrize("target_exists", [False, True])
def test_symlink_and_dangling_symlink_targets_are_untouched(tmp_path, target_exists):
    store = store_at(tmp_path)
    store.path.parent.mkdir(mode=0o700)
    target = tmp_path / "untouched"
    if target_exists:
        target.write_bytes(b"sentinel")
    store.path.symlink_to(target)
    for operation in (lambda: store.record("list", "ok"), lambda: store.recent()):
        with pytest.raises(AuditError, match="unsafe"):
            operation()
    assert target.exists() == target_exists
    if target_exists:
        assert target.read_bytes() == b"sentinel"


def test_symlink_parent_and_hardlink_database_are_rejected(tmp_path):
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    store = ContentMinimizedAuditStore(link / "audit.sqlite3", AuditSettings(mode="sqlite"))
    with pytest.raises(AuditError, match="unsafe"):
        store.record("list", "ok")
    assert list(target.iterdir()) == []
    store = store_at(tmp_path)
    store.record("list", "ok")
    before = store.path.read_bytes()
    os.link(store.path, tmp_path / "hardlink")
    with pytest.raises(AuditError, match="unsafe"):
        store.record("list", "ok")
    assert store.path.read_bytes() == before


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode contract")
@pytest.mark.parametrize("part", ["file", "directory"])
def test_unsafe_permissions_are_rejected_not_silently_chmodded(tmp_path, part):
    store = store_at(tmp_path)
    store.record("list", "ok")
    target = store.path if part == "file" else store.path.parent
    mode = 0o644 if part == "file" else 0o755
    target.chmod(mode)
    with pytest.raises(AuditError, match="permissions"):
        store.record("list", "ok")
    assert stat.S_IMODE(target.stat().st_mode) == mode


def test_replaced_database_is_rejected(tmp_path):
    store = store_at(tmp_path)
    store.record("list", "ok")
    original = store.path.with_suffix(".original")
    store.path.rename(original)
    replacement = original.read_bytes()
    store.path.write_bytes(replacement)
    store.path.chmod(0o600)
    with pytest.raises(AuditError, match="identity changed"):
        store.record("get", "ok")
    assert store.path.read_bytes() == replacement


def test_sidecar_symlink_is_rejected_without_touching_target(tmp_path):
    store = store_at(tmp_path)
    store.record("list", "ok")
    target = tmp_path / "untouched"
    target.write_bytes(b"not a journal")
    Path(str(store.path) + "-journal").symlink_to(target)
    with pytest.raises(AuditError, match="unsafe"):
        store.record("get", "ok")
    assert target.read_bytes() == b"not a journal"


def test_valid_legacy_schema_is_adopted_without_dropping_records(tmp_path):
    store = store_at(tmp_path)
    store.path.parent.mkdir(mode=0o700)
    now = 1_800_000_000
    with sqlite3.connect(store.path) as connection:
        connection.execute(_TABLE_SQL)
        connection.execute(_INDEX_SQL)
        connection.execute("INSERT INTO audit_events VALUES(1,?,?,?,?)", (now, "list", "ok", 2))
    store.path.chmod(0o600)
    store = ContentMinimizedAuditStore(store.path, store.settings, clock=lambda: now)
    before = store.path.read_bytes()
    assert store.recent()[0]["item_count"] == 2
    assert store.path.read_bytes() == before  # reading does not migrate
    store.record("get", "ok", 1)
    assert [r["operation"] for r in store.recent()] == ["get", "list"]
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("PRAGMA application_id").fetchone() == (_APPLICATION_ID,)
        assert connection.execute("PRAGMA user_version").fetchone() == (1,)


@pytest.mark.parametrize("tamper", ["trigger", "table", "identity"])
def test_foreign_schema_or_trigger_fails_without_changing_records(tmp_path, tamper):
    store = store_at(tmp_path)
    store.record("list", "ok")
    with sqlite3.connect(store.path) as connection:
        if tamper == "trigger":
            connection.execute("CREATE TRIGGER injected AFTER INSERT ON audit_events BEGIN DELETE FROM audit_events; END")
        elif tamper == "table":
            connection.execute("CREATE TABLE unrelated(value TEXT)")
        else:
            connection.execute("PRAGMA application_id=123")
    before = store.path.read_bytes()
    for operation in (lambda: store.record("get", "ok"), lambda: store.recent()):
        with pytest.raises(AuditError, match="incompatible"):
            operation()
    assert store.path.read_bytes() == before


def test_disabled_mode_and_untrusted_outcome_cannot_write(tmp_path):
    store = store_at(tmp_path)
    for outcome in ("private@example.invalid", "SECRET VALUE", "ok\n", "", [], 5):
        with pytest.raises(AuditError, match="outcome"):
            store.record("list", outcome)
    assert not store.path.parent.exists()
    disabled = ContentMinimizedAuditStore(store.path, AuditSettings())
    with pytest.raises(AuditError, match="disabled"):
        disabled.record("list", "ok")
    assert not store.path.exists()


def test_database_size_limit_is_enforced_before_write_and_rolls_back(tmp_path):
    store = store_at(tmp_path, max_database_bytes=1_048_576)
    store.record("list", "ok", 1)
    before = store.recent()
    # Simulate SQLite running out of pages within this transaction.
    original = store._enforce_page_limit
    def limit_to_current(connection, *, writable):
        original(connection, writable=writable)
        if writable:
            pages = connection.execute("PRAGMA page_count").fetchone()[0]
            connection.execute(f"PRAGMA max_page_count={pages}")
    store._enforce_page_limit = limit_to_current
    saw_full = False
    for _ in range(150):
        try:
            store.record("draft-reply-all-create", "draft-storage-incompatible", 100_000)
        except AuditError:
            saw_full = True
            break
    assert saw_full
    rows_before = store.recent(limit=100)
    with pytest.raises(AuditError):
        store.record("draft-reply-all-create", "draft-storage-incompatible", 100_000)
    assert store.recent(limit=100) == rows_before
    assert store.path.stat().st_size <= store.settings.max_database_bytes
    assert before[0]["operation"] == "list"


def test_oversize_existing_database_is_rejected_before_connect(tmp_path, monkeypatch):
    store = store_at(tmp_path, max_database_bytes=1_048_576)
    store.path.parent.mkdir(mode=0o700)
    with store.path.open("wb") as f:
        f.truncate(1_048_577)
    store.path.chmod(0o600)
    def forbidden(*args, **kwargs):
        pytest.fail("oversized database must not be connected")
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    with pytest.raises(AuditError, match="size limit"):
        store.record("list", "ok")


def test_retention_and_cross_instance_writes_are_bounded(tmp_path):
    store = store_at(tmp_path, max_events=8, retention_days=1)
    store._clock = lambda: 1_800_000_000
    store.record("list", "ok")
    store._clock = lambda: 1_800_100_000
    assert store.recent() == []  # expired rows not returned even before next write
    peers = [ContentMinimizedAuditStore(store.path, store.settings, clock=store._clock) for _ in range(4)]
    def write(peer):
        for _ in range(5):
            peer.record("get", "ok", 1)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write, peers))
    rows = store.recent()
    assert len(rows) == 8
    assert all(r["operation"] == "get" for r in rows)
    store.close()
    reopened = ContentMinimizedAuditStore(store.path, store.settings, clock=lambda: 1_800_100_000)
    assert reopened.recent() == rows


def test_every_registered_operation_can_be_recorded(tmp_path):
    store = store_at(tmp_path)
    for operation in sorted(_ALLOWED_OPERATIONS):
        store.record(operation, "ok", 1)
    assert {r["operation"] for r in store.recent()} == _ALLOWED_OPERATIONS
