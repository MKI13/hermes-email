"""Content-minimized, profile-scoped SQLite audit ledger."""
from __future__ import annotations
import os, sqlite3, threading, time
from pathlib import Path
from .config import AuditSettings

_ALLOWED_OPERATIONS={"list","get","search","thread","draft-create","draft-list","draft-get","draft-update","draft-trash","draft-restore","health"}

class AuditError(RuntimeError): pass

class ContentMinimizedAuditStore:
    def __init__(self,path:Path,settings:AuditSettings):
        self.path=Path(path); self.settings=settings; self._lock=threading.RLock(); self._closed=False
    def close(self):
        with self._lock: self._closed=True
    def record(self,operation:str,outcome:str,item_count:int=0)->None:
        if operation not in _ALLOWED_OPERATIONS: raise AuditError("invalid audit operation")
        if not isinstance(outcome,str) or not outcome or len(outcome)>64 or not outcome.isascii(): raise AuditError("invalid audit outcome")
        if isinstance(item_count,bool) or not isinstance(item_count,int) or not 0<=item_count<=100000: raise AuditError("invalid audit item count")
        with self._lock:
            if self._closed: raise AuditError("audit store is closed")
            self._prepare_path(); c=sqlite3.connect(self.path,timeout=5)
            try:
                c.execute("PRAGMA trusted_schema=OFF"); c.execute("PRAGMA journal_mode=DELETE"); c.execute("PRAGMA synchronous=FULL")
                c.execute("CREATE TABLE IF NOT EXISTS audit_events(id INTEGER PRIMARY KEY AUTOINCREMENT,created_at INTEGER NOT NULL,operation TEXT NOT NULL,outcome TEXT NOT NULL,item_count INTEGER NOT NULL)")
                c.execute("CREATE INDEX IF NOT EXISTS audit_created ON audit_events(created_at)")
                now=int(time.time()); c.execute("BEGIN IMMEDIATE")
                c.execute("INSERT INTO audit_events(created_at,operation,outcome,item_count) VALUES(?,?,?,?)",(now,operation,outcome,item_count))
                cutoff=now-self.settings.retention_days*86400; c.execute("DELETE FROM audit_events WHERE created_at < ?",(cutoff,))
                c.execute("DELETE FROM audit_events WHERE id NOT IN (SELECT id FROM audit_events ORDER BY id DESC LIMIT ?)",(self.settings.max_events,))
                c.commit()
            except sqlite3.DatabaseError as e:
                c.rollback(); raise AuditError("audit operation failed") from None
            finally: c.close()
            if self.path.stat().st_size>self.settings.max_database_bytes: raise AuditError("audit database exceeds size limit")
    def recent(self,limit:int=25)->list[dict[str,int|str]]:
        if isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=100: raise AuditError("invalid audit limit")
        with self._lock:
            self._prepare_path(); c=sqlite3.connect(self.path)
            try:
                rows=c.execute("SELECT created_at,operation,outcome,item_count FROM audit_events ORDER BY id DESC LIMIT ?",(limit,)).fetchall()
                return [{"created_at":int(a),"operation":b,"outcome":d,"item_count":int(e)} for a,b,d,e in rows]
            finally:c.close()
    def _prepare_path(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        if os.name=="posix":
            os.chmod(self.path.parent,0o700)
            if self.path.exists() and self.path.is_symlink(): raise AuditError("audit path is unsafe")
            if self.path.exists(): os.chmod(self.path,0o600)
