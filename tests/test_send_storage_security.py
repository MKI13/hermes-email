"""No real SMTP: validate persistence failures before the transport boundary."""
from __future__ import annotations

import os
import shutil
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from hermes_email.send_files import SendStorageError
from hermes_email.send_storage import APPLICATION_ID, META_SQL, TABLE_V1, TABLE_V2, SecureSendDatabase
from hermes_email.send_orchestration import IdempotentSendOrchestrator, SqliteSendIntentStore
from test_send_orchestration import candidate, FakeTransport, OPERATION_ID


def create_ledger(root: Path) -> SqliteSendIntentStore:
    ledger = SqliteSendIntentStore(root / 'data')
    IdempotentSendOrchestrator(ledger, FakeTransport()).send_once(OPERATION_ID, candidate())
    return ledger


def assert_no_submission(ledger: SqliteSendIntentStore) -> None:
    transport = FakeTransport()
    with pytest.raises(SendStorageError):
        IdempotentSendOrchestrator(ledger, transport).send_once('send-security-test-0002', candidate(revision=2))
    assert transport.calls == 0


def test_first_creation_is_private_even_with_permissive_umask(tmp_path):
    prior = os.umask(0)
    try:
        ledger = create_ledger(tmp_path)
    finally:
        os.umask(prior)
    for p in ledger.path.parent.iterdir():
        assert p.stat().st_mode & 0o777 == 0o600
    assert ledger.path.parent.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize('mode', [0o644, 0o660, 0o400, 0o000])
def test_unsafe_or_readonly_file_is_rejected_without_repair(tmp_path, mode):
    ledger = create_ledger(tmp_path)
    ledger.path.chmod(mode)
    assert_no_submission(ledger)
    assert ledger.path.stat().st_mode & 0o777 == mode


def test_readonly_parent_is_rejected(tmp_path):
    ledger = create_ledger(tmp_path)
    ledger.path.parent.chmod(0o500)
    try:
        assert_no_submission(ledger)
    finally:
        ledger.path.parent.chmod(0o700)


@pytest.mark.parametrize('damage', ['corrupt', 'truncated', 'deleted', 'replacement', 'foreign-schema', 'invalid-row', 'wrong-version', 'trigger', 'wal'])
def test_damaged_or_replaced_ledger_blocks_all_new_smtp(tmp_path, damage):
    ledger = create_ledger(tmp_path)
    if damage == 'corrupt':
        ledger.path.write_bytes(b'not a sqlite file')
    elif damage == 'truncated':
        ledger.path.write_bytes(b'')
    elif damage == 'deleted':
        ledger.path.unlink()
    elif damage == 'replacement':
        other = tmp_path/'replacement'
        shutil.copyfile(ledger.path, other); other.chmod(0o600); other.replace(ledger.path)
    else:
        with sqlite3.connect(ledger.path) as c:
            if damage == 'foreign-schema':
                c.execute('CREATE TABLE unrelated(data TEXT)')
            elif damage == 'invalid-row':
                c.execute("UPDATE send_intents SET request_digest='bad'")
            elif damage == 'wrong-version':
                c.execute('PRAGMA user_version=999')
            elif damage == 'trigger':
                c.execute('CREATE TRIGGER surprise AFTER INSERT ON send_intents BEGIN DELETE FROM send_intents; END')
            else:
                c.execute('PRAGMA journal_mode=WAL')
    assert_no_submission(ledger)
    assert_no_submission(SqliteSendIntentStore(ledger.path.parent))
    if damage == 'deleted':
        assert not ledger.path.exists()


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'fifo', 'directory', 'parent-link', 'journal-link', 'anchor-link'])
def test_unsafe_paths_are_rejected(tmp_path, kind):
    ledger = create_ledger(tmp_path)
    target = tmp_path/'target'
    target.write_text('untouched'); target.chmod(0o600)
    if kind in {'symlink','hardlink','fifo','directory'}:
        ledger.path.unlink()
        if kind == 'symlink': ledger.path.symlink_to(target)
        elif kind == 'hardlink': os.link(target, ledger.path)
        elif kind == 'fifo': os.mkfifo(ledger.path, 0o600)
        else: ledger.path.mkdir()
    elif kind == 'parent-link':
        old = ledger.path.parent; moved = tmp_path/'moved'; old.rename(moved); old.symlink_to(moved, target_is_directory=True)
    elif kind == 'journal-link':
        Path(str(ledger.path)+'-journal').symlink_to(target)
    else:
        p=Path(str(ledger.path)+'.identity'); p.unlink(); p.symlink_to(target)
    assert_no_submission(ledger)
    assert target.read_text() == 'untouched'
    assert target.stat().st_mode & 0o777 == 0o600


def test_replacement_between_begin_and_transport_is_detected(tmp_path, monkeypatch):
    ledger = create_ledger(tmp_path)
    original = ledger.begin
    def swap(*a, **kw):
        result = original(*a, **kw)
        replacement = tmp_path/'swap'
        shutil.copyfile(ledger.path, replacement); replacement.chmod(0o600); replacement.replace(ledger.path)
        return result
    monkeypatch.setattr(ledger, 'begin', swap)
    assert_no_submission(ledger)


def test_capacity_does_not_prune_previously_accepted_sends(tmp_path):
    ledger = SqliteSendIntentStore(tmp_path/'data', max_intents=1)
    transport = FakeTransport(); orch = IdempotentSendOrchestrator(ledger,transport)
    orch.send_once(OPERATION_ID,candidate())
    with pytest.raises(SendStorageError, match='capacity'):
        orch.send_once('send-operation-0002',candidate(revision=2))
    assert transport.calls == 1
    assert ledger.get(OPERATION_ID).state == 'accepted'
    assert orch.send_once(OPERATION_ID,candidate()).replayed
    with sqlite3.connect(ledger.path) as c:
        assert c.execute('SELECT COUNT(*) FROM send_intents').fetchone() == (1,)


def test_page_limit_prevents_unbounded_writes_and_preserves_ledger(tmp_path):
    ledger = create_ledger(tmp_path)
    database = SecureSendDatabase(ledger.path, max_database_bytes=1_048_576)
    with pytest.raises(SendStorageError):
        with database.transaction() as c:
            c.execute('CREATE TABLE test_fill(payload BLOB)')
            c.execute('INSERT INTO test_fill VALUES (zeroblob(2000000))')
    assert ledger.path.stat().st_size <= 1_048_576
    assert ledger.get(OPERATION_ID).state == 'accepted'


def test_oversized_existing_file_rejected_without_sqlite_open(tmp_path, monkeypatch):
    ledger = create_ledger(tmp_path)
    with ledger.path.open('r+b') as f: f.truncate(2_000_000)
    small = SqliteSendIntentStore(ledger.path.parent,max_database_bytes=1_048_576)
    monkeypatch.setattr(sqlite3,'connect',lambda *a,**k: pytest.fail('must reject before SQLite'))
    assert_no_submission(small)


@pytest.mark.parametrize('version',[1,2])
def test_legacy_migration_preserves_every_record_and_uniqueness(tmp_path,version):
    path = tmp_path/'data'; path.mkdir(mode=0o700)
    ledger = SqliteSendIntentStore(path)
    with sqlite3.connect(ledger.path) as c:
        c.execute(META_SQL); c.execute('INSERT INTO meta VALUES (?)',(version,)); c.execute(TABLE_V1 if version==1 else TABLE_V2)
        for i,state in enumerate(('accepted','definite-failure','delivery-unknown','dispatching')):
            c.execute("INSERT INTO send_intents (operation_id,draft_id,revision,confirmation_id,request_digest,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                      (f'legacy-operation-{i:04d}','draft_legacy',i+1,'legacy-confirmation-0001','a'*64,state,'2026-09-05T00:00:00+00:00','2026-09-05T00:00:00+00:00'))
    ledger.path.chmod(0o600)
    assert ledger.recover_interrupted_dispatches() == 1
    with sqlite3.connect(ledger.path) as c:
        assert c.execute('SELECT COUNT(*) FROM send_intents').fetchone() == (4,)
        assert c.execute('PRAGMA application_id').fetchone() == (APPLICATION_ID,)
        assert c.execute('SELECT schema_version FROM meta').fetchone() == (3,)
        assert [r[0] for r in c.execute('SELECT state FROM send_intents ORDER BY revision')] == ['accepted','definite-failure','delivery-unknown','delivery-unknown']
    assert Path(str(ledger.path)+'.identity').exists()


def test_invalid_legacy_file_is_not_partially_migrated(tmp_path):
    root=tmp_path/'data'; root.mkdir(mode=0o700)
    ledger=SqliteSendIntentStore(root)
    with sqlite3.connect(ledger.path) as c:
        c.execute(META_SQL);c.execute('INSERT INTO meta VALUES (1)');c.execute(TABLE_V1)
        c.execute("INSERT INTO send_intents VALUES(?,?,?,?,?,?,?,?)",(OPERATION_ID,'draft_x',1,'confirmation-0001','invalid','accepted','2026-09-05T00:00:00+00:00','2026-09-05T00:00:00+00:00'))
    ledger.path.chmod(0o600)
    before=ledger.path.read_bytes()
    assert_no_submission(ledger)
    assert ledger.path.read_bytes() == before
    assert not Path(str(ledger.path)+'.identity').exists()


def test_closed_ledger_cannot_be_reopened(tmp_path):
    ledger=create_ledger(tmp_path); ledger.close()
    assert_no_submission(ledger)


def test_fsync_failure_prevents_submission(tmp_path,monkeypatch):
    def fail(*a): raise OSError('SYNTHETIC PRIVATE DETAIL')
    monkeypatch.setattr(os,'fsync',fail)
    ledger=SqliteSendIntentStore(tmp_path/'data')
    transport=FakeTransport()
    with pytest.raises(SendStorageError) as error:
        IdempotentSendOrchestrator(ledger,transport).send_once(OPERATION_ID,candidate())
    assert 'PRIVATE' not in str(error.value)
    assert transport.calls == 0
