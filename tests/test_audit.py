import sqlite3
from pathlib import Path

from hermes_email.audit import ContentMinimizedAuditStore
from hermes_email.config import AuditSettings


def test_audit_stores_only_minimized_fields(tmp_path: Path):
    path=tmp_path/'data'/'email-audit.sqlite3'
    store=ContentMinimizedAuditStore(path, AuditSettings(mode='sqlite'))
    store.record('get','ok',1)
    rows=store.recent()
    assert rows and rows[0]['operation']=='get' and rows[0]['outcome']=='ok' and rows[0]['item_count']==1
    with sqlite3.connect(path) as c:
        cols=[r[1] for r in c.execute('PRAGMA table_info(audit_events)')]
        assert cols==['id','created_at','operation','outcome','item_count']
        sql=' '.join(r[0] for r in c.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"))
        for forbidden in ('subject','body','address','recipient','message_id','draft_id','query','filename','secret'):
            assert forbidden not in sql.casefold()


def test_audit_bounds_and_private_permissions(tmp_path: Path):
    path=tmp_path/'data'/'email-audit.sqlite3'
    store=ContentMinimizedAuditStore(path, AuditSettings(mode='sqlite',max_events=2))
    for op in ('list','get','search'): store.record(op,'ok',1)
    assert [r['operation'] for r in store.recent()] == ['search','get']
    if __import__('os').name=='posix':
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.parent.stat().st_mode & 0o777 == 0o700
