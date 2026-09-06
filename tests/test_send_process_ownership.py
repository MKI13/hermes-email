"""Real OS processes and crash/stop signals; only synthetic SMTP transports."""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import signal
import sqlite3
import threading
import time
import uuid
from pathlib import Path

import pytest

from hermes_email.send_files import FileLease, SendBusyError, SendStorageError
from hermes_email.send_orchestration import (
    IdempotentSendOrchestrator, SqliteSendIntentStore, SendOwnershipError,
)
from hermes_email.send_storage import APPLICATION_ID, META_SQL, TABLE_V2, IDENTITY_SQL
from hermes_email.smtp import SmtpSubmissionResult
from test_send_orchestration import candidate, FakeTransport, BlockingTransport, OPERATION_ID


class DiskRelease:
    """Crash-safe test control: a killed child cannot poison a shared semaphore."""
    def __init__(self,path):self.path=Path(path)
    def set(self):self.path.write_bytes(b'release')
    def wait(self,seconds):
        deadline=time.monotonic()+seconds
        while not self.path.exists():
            if time.monotonic()>=deadline:return False
            time.sleep(0.01)
        return True


class ProcessTransport:
    def __init__(self, log, messages, release):
        self.log, self.messages, self.release = Path(log), messages, release

    def submit_once(self, submission):
        fd=os.open(self.log,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
        try:
            os.write(fd, f'{os.getpid()}\n'.encode());os.fsync(fd)
        finally:
            os.close(fd)
        self.messages.put(('entered',os.getpid()))
        if not self.release.wait(25):
            raise RuntimeError('test worker release timed out')
        return SmtpSubmissionResult()


def _worker(root, log, start, release, messages):
    ledger=SqliteSendIntentStore(Path(root))
    try:
        sender=IdempotentSendOrchestrator(ledger,ProcessTransport(log,messages,release))
        messages.put(('ready',os.getpid()))
        if not start.wait(25): raise RuntimeError('test start timed out')
        record=sender.send_once(OPERATION_ID,candidate())
        messages.put(('done',os.getpid(),record.state,record.replayed))
    except Exception as e:
        messages.put(('error',os.getpid(),type(e).__name__,str(e)))
        raise


def _finish(processes, release):
    release.set()
    for p in processes:
        p.join(5)
        if p.is_alive():
            p.kill();p.join(5)


def _launch(tmp_path,count=1):
    ctx=mp.get_context('spawn')
    start,release,messages=ctx.Event(),DiskRelease(tmp_path/'release'),ctx.Queue()
    root,log=tmp_path/'data',tmp_path/'smtp-attempts'
    processes=[ctx.Process(target=_worker,args=(str(root),str(log),start,release,messages)) for _ in range(count)]
    for p in processes:p.start()
    for _ in processes:
        item=messages.get(timeout=20);assert item[0]=='ready',item
    start.set()
    return root,log,processes,release,messages


def test_two_simultaneous_processes_only_one_reaches_transport(tmp_path):
    root,log,processes,release,messages=_launch(tmp_path,2)
    try:
        first=messages.get(timeout=20);second=messages.get(timeout=20)
        by_kind={item[0]:item for item in (first,second)}
        assert set(by_kind)=={'entered','done'},(first,second)
        assert by_kind['done'][2:] == ('dispatching',True)
        observer=SqliteSendIntentStore(root)
        assert observer.recover_interrupted_dispatches()==0
        assert observer.get(OPERATION_ID).state=='dispatching'
        assert len(log.read_text().splitlines())==1
        release.set()
        done=messages.get(timeout=20)
        assert done[0]=='done' and done[2:] == ('accepted',False),done
        assert observer.get(OPERATION_ID).state=='accepted'
    finally:
        _finish(processes,release)
    assert all(p.exitcode==0 for p in processes)
    assert len(log.read_text().splitlines())==1


def test_killed_owner_recovers_unknown_without_redispatch(tmp_path):
    root,log,processes,release,messages=_launch(tmp_path)
    try:
        entered=messages.get(timeout=20);assert entered[0]=='entered',entered
        processes[0].kill();processes[0].join(10)
        observer=SqliteSendIntentStore(root)
        assert observer.recover_interrupted_dispatches()==1
        record=observer.get(OPERATION_ID)
        assert record.state=='delivery-unknown' and record.manual_review_required
        assert record.message_id==candidate().message_id
        transport=FakeTransport()
        replay=IdempotentSendOrchestrator(observer,transport).send_once(OPERATION_ID,candidate())
        assert replay.replayed and replay.state=='delivery-unknown' and transport.calls==0
        assert observer.finish(OPERATION_ID,'accepted').state=='delivery-unknown'
        assert len(log.read_text().splitlines())==1
    finally:
        _finish(processes,release)


@pytest.mark.skipif(not hasattr(signal,'SIGSTOP'),reason='requires POSIX process stop/resume')
def test_paused_process_keeps_ownership_without_timeout_steal(tmp_path):
    root,log,processes,release,messages=_launch(tmp_path)
    try:
        assert messages.get(timeout=20)[0]=='entered'
        os.kill(processes[0].pid,signal.SIGSTOP)
        observer=SqliteSendIntentStore(root)
        for _ in range(3):
            assert observer.recover_interrupted_dispatches()==0
            assert observer.get(OPERATION_ID).state=='dispatching'
        with pytest.raises(SendOwnershipError):observer.finish(OPERATION_ID,'accepted')
        with pytest.raises(SendBusyError):observer.begin('another-operation-0002',candidate(revision=2))
        assert observer.get('another-operation-0002') is None
        os.kill(processes[0].pid,signal.SIGCONT)
        release.set()
        done=messages.get(timeout=20);assert done[0]=='done' and done[2]=='accepted',done
    finally:
        if processes[0].is_alive():os.kill(processes[0].pid,signal.SIGCONT)
        _finish(processes,release)
    assert len(log.read_text().splitlines())==1


def _fork_reader(ledger, connection):
    replay=ledger.begin(OPERATION_ID,candidate())
    try:
        ledger.finish(OPERATION_ID,'accepted')
    except SendOwnershipError:
        connection.send((replay.state,replay.replayed,'ownership-denied'))
    else:
        connection.send(('invalid','invalid','invalid'))
    ledger.close()
    connection.close()


@pytest.mark.skipif('fork' not in mp.get_all_start_methods(),reason='requires fork')
def test_fork_child_cannot_inherit_or_release_parent_ownership(tmp_path):
    ledger=SqliteSendIntentStore(tmp_path/'data');ledger.begin(OPERATION_ID,candidate())
    ctx=mp.get_context('fork');parent,child=ctx.Pipe(False)
    process=ctx.Process(target=_fork_reader,args=(ledger,child));process.start();child.close()
    try:
        assert parent.poll(10)
        assert parent.recv()==('dispatching',True,'ownership-denied')
        process.join(10);assert process.exitcode==0
        assert ledger.get(OPERATION_ID).state=='dispatching'
        ledger.verify_dispatch(OPERATION_ID,candidate())
        assert ledger.finish(OPERATION_ID,'accepted').state=='accepted'
    finally:
        if process.is_alive():process.kill();process.join(5)
        parent.close()


def test_close_and_foreign_thread_finish_do_not_release_live_owner(tmp_path):
    ledger=SqliteSendIntentStore(tmp_path/'data');transport=BlockingTransport()
    sender=IdempotentSendOrchestrator(ledger,transport);results=[]
    worker=threading.Thread(target=lambda:results.append(sender.send_once(OPERATION_ID,candidate())))
    worker.start();assert transport.entered.wait(5)
    try:
        with pytest.raises(SendBusyError):ledger.close()
        with pytest.raises(SendOwnershipError):ledger.finish(OPERATION_ID,'accepted')
        observer=SqliteSendIntentStore(ledger.path.parent)
        assert observer.get(OPERATION_ID).state=='dispatching'
    finally:
        transport.release.set();worker.join(5)
    assert len(results)==1 and results[0].state=='accepted' and transport.calls==1
    ledger.close()


@pytest.mark.parametrize('damage',['deleted','replaced'])
def test_dispatch_lock_identity_is_bound_across_store_instances(tmp_path,damage):
    ledger=SqliteSendIntentStore(tmp_path/'data')
    IdempotentSendOrchestrator(ledger,FakeTransport()).send_once(OPERATION_ID,candidate())
    lock=Path(str(ledger.path)+'.dispatch')
    if damage=='deleted':lock.unlink()
    else:
        replacement=tmp_path/'new-lock';replacement.write_bytes(b'');replacement.chmod(0o600);replacement.replace(lock)
    transport=FakeTransport()
    with pytest.raises(SendStorageError):
        IdempotentSendOrchestrator(SqliteSendIntentStore(ledger.path.parent),transport).send_once(OPERATION_ID,candidate())
    assert transport.calls==0
    if damage=='deleted':assert not lock.exists()


def test_finish_storage_failure_remains_unknown_and_not_retryable(tmp_path):
    ledger=SqliteSendIntentStore(tmp_path/'data')
    class DamageAfterSubmission(FakeTransport):
        def submit_once(self,submission):
            self.calls+=1;ledger.path.chmod(0o400);return SmtpSubmissionResult()
    transport=DamageAfterSubmission()
    try:
        with pytest.raises(SendStorageError):
            IdempotentSendOrchestrator(ledger,transport).send_once(OPERATION_ID,candidate())
    finally:
        ledger.path.chmod(0o600)
    fresh=SqliteSendIntentStore(ledger.path.parent)
    assert fresh.recover_interrupted_dispatches()==1
    retry_transport=FakeTransport()
    assert IdempotentSendOrchestrator(fresh,retry_transport).send_once(OPERATION_ID,candidate()).state=='delivery-unknown'
    assert transport.calls==1 and retry_transport.calls==0


def test_unknown_transport_return_is_not_reported_accepted(tmp_path):
    class Uncertain(FakeTransport):
        def submit_once(self,submission):self.calls+=1;return None
    ledger=SqliteSendIntentStore(tmp_path/'data');transport=Uncertain()
    assert IdempotentSendOrchestrator(ledger,transport).send_once(OPERATION_ID,candidate()).state=='delivery-unknown'


def test_schema3_identity_upgrade_preserves_all_evidence(tmp_path):
    root=tmp_path/'data';root.mkdir(mode=0o700);ledger=SqliteSendIntentStore(root,legacy_workers_stopped=True)
    ledger_uuid=uuid.uuid4().hex
    with sqlite3.connect(ledger.path) as c:
        c.execute(META_SQL);c.execute(TABLE_V2);c.execute(IDENTITY_SQL)
        c.execute('INSERT INTO meta VALUES (3)');c.execute('INSERT INTO ledger_identity VALUES (1,?)',(ledger_uuid,))
        c.execute(f'PRAGMA application_id={APPLICATION_ID}');c.execute('PRAGMA user_version=3')
        for i,state in enumerate(('accepted','definite-failure','delivery-unknown','dispatching')):
            c.execute('INSERT INTO send_intents VALUES(?,?,?,?,?,?,?,?,?)',
                      (f'legacy-operation-{i:04d}','draft_legacy',i+1,'legacy-confirmation-0001','a'*64,state,
                       'old-owner' if state=='dispatching' else None,'2026-09-05T00:00:00+00:00','2026-09-05T00:00:00+00:00'))
    ledger.path.chmod(0o600);st=ledger.path.stat()
    anchor=Path(str(ledger.path)+'.identity');anchor.write_text(json.dumps([1,st.st_dev,st.st_ino,ledger_uuid],separators=(',',':')));anchor.chmod(0o600)
    assert ledger.recover_interrupted_dispatches()==1
    with sqlite3.connect(ledger.path) as c:
        assert c.execute('SELECT COUNT(*) FROM send_intents').fetchone()==(4,)
        assert c.execute('SELECT schema_version FROM meta').fetchone()==(4,)
        assert c.execute('SELECT ledger_uuid FROM ledger_identity').fetchone()==(ledger_uuid,)
        assert c.execute('SELECT COUNT(*) FROM send_intents WHERE message_id IS NULL AND message_date IS NULL').fetchone()==(4,)
    assert json.loads(anchor.read_text())[0]==2


def test_missing_lock_backend_fails_closed(tmp_path,monkeypatch):
    def unavailable(*a,**k):raise SendStorageError('locking unavailable')
    monkeypatch.setattr(FileLease,'acquire',unavailable)
    transport=FakeTransport()
    with pytest.raises(SendStorageError):
        IdempotentSendOrchestrator(SqliteSendIntentStore(tmp_path/'data'),transport).send_once(OPERATION_ID,candidate())
    assert transport.calls==0


class LoopbackSmtpTransport:
    """Test-only plaintext loopback transport, never a production provider."""
    def __init__(self,port):self.port=port
    def submit_once(self,submission):
        import smtplib
        with smtplib.SMTP('127.0.0.1',self.port,timeout=15) as client:
            refused=client.sendmail(submission.envelope_sender,submission.envelope_recipients,submission.message_bytes)
            assert refused=={}
        return SmtpSubmissionResult()


def _smtp_worker(root,port,start,messages):
    sender=IdempotentSendOrchestrator(SqliteSendIntentStore(Path(root)),LoopbackSmtpTransport(port))
    messages.put(('ready',os.getpid()))
    assert start.wait(20)
    record=sender.send_once(OPERATION_ID,candidate())
    messages.put(('done',os.getpid(),record.state,record.replayed))


def test_two_processes_produce_one_actual_loopback_smtp_data_transaction(tmp_path):
    import socketserver
    received=[];release=threading.Event();data_entered=threading.Event()
    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.wfile.write(b'220 test-sink\r\n')
            while True:
                line=self.rfile.readline(4096)
                if not line:break
                command=line.split(None,1)[0].upper()
                if command in {b'EHLO',b'HELO'}:self.wfile.write(b'250 test-sink\r\n')
                elif command in {b'MAIL',b'RCPT',b'RSET'}:self.wfile.write(b'250 OK\r\n')
                elif command==b'DATA':
                    self.wfile.write(b'354 End with dot\r\n')
                    data=[]
                    while True:
                        chunk=self.rfile.readline(4096)
                        if chunk==b'.\r\n':break
                        if not chunk:return
                        data.append(chunk)
                    received.append(b''.join(data));data_entered.set()
                    assert release.wait(20)
                    self.wfile.write(b'250 accepted\r\n')
                elif command==b'QUIT':self.wfile.write(b'221 bye\r\n');break
                else:self.wfile.write(b'500 invalid\r\n')
    class Server(socketserver.ThreadingTCPServer):
        daemon_threads=True
    with Server(('127.0.0.1',0),Handler) as server:
        server_thread=threading.Thread(target=server.serve_forever,daemon=True);server_thread.start()
        ctx=mp.get_context('spawn');start=ctx.Event();messages=ctx.Queue()
        processes=[ctx.Process(target=_smtp_worker,args=(str(tmp_path/'data'),server.server_address[1],start,messages)) for _ in range(2)]
        try:
            for p in processes:p.start()
            for _ in processes:assert messages.get(timeout=20)[0]=='ready'
            start.set()
            replay=messages.get(timeout=20)
            assert replay[0]=='done' and replay[2:]==('dispatching',True),replay
            assert data_entered.wait(10)
            assert SqliteSendIntentStore(tmp_path/'data').get(OPERATION_ID).state=='dispatching'
            assert received==[candidate().message_bytes]
            release.set()
            sent=messages.get(timeout=20)
            assert sent[0]=='done' and sent[2:]==('accepted',False),sent
        finally:
            release.set()
            for p in processes:
                p.join(5)
                if p.is_alive():p.kill();p.join(5)
            server.shutdown();server_thread.join(5)
        assert all(p.exitcode==0 for p in processes)
        assert received==[candidate().message_bytes]


def test_legacy_pending_dispatch_requires_explicit_offline_migration(tmp_path):
    from hermes_email.send_storage import TABLE_V1
    root=tmp_path/'data';root.mkdir(mode=0o700);ledger=SqliteSendIntentStore(root)
    with sqlite3.connect(ledger.path) as c:
        c.execute(META_SQL);c.execute(TABLE_V1);c.execute('INSERT INTO meta VALUES (1)')
        c.execute('INSERT INTO send_intents VALUES(?,?,?,?,?,?,?,?)',
                  (OPERATION_ID,'draft_legacy',1,'legacy-confirmation-0001','a'*64,'dispatching',
                   '2026-09-05T00:00:00+00:00','2026-09-05T00:00:00+00:00'))
    ledger.path.chmod(0o600);before=ledger.path.read_bytes();transport=FakeTransport()
    with pytest.raises(SendStorageError,match='offline migration'):
        IdempotentSendOrchestrator(ledger,transport)
    assert transport.calls==0 and ledger.path.read_bytes()==before
    assert not Path(str(ledger.path)+'.identity').exists()
    offline=SqliteSendIntentStore(root,legacy_workers_stopped=True)
    assert offline.recover_interrupted_dispatches()==1
    assert offline.get(OPERATION_ID).state=='delivery-unknown'


def test_lock_path_os_error_is_redacted(tmp_path,monkeypatch):
    import hermes_email.send_files as files
    def fail(*a,**k):raise OSError('SYNTHETIC PRIVATE PATH')
    monkeypatch.setattr(files,'open_private',fail)
    with pytest.raises(SendStorageError) as error:FileLease(tmp_path/'lock').acquire()
    assert 'PRIVATE' not in str(error.value)


def test_disappearing_lock_verify_error_is_redacted(tmp_path):
    lease=FileLease(tmp_path/'lock');assert lease.acquire()
    try:
        lease.path.unlink()
        with pytest.raises(SendStorageError) as error:lease.verify()
        assert str(tmp_path) not in str(error.value)
    finally:lease.close()
