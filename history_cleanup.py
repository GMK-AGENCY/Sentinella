"""Remove display journals without touching delivery ownership or replay guards.

Only known files directly inside the supplied data root's logs are writable.
Mixed append journals retain byte offsets: cleared payloads become padded JSON
tombstones. They are overwritten, not merely hidden by a UI filter. Runtime
state, drafts, deduplication, reservations and receipt ledgers are never removed.
"""
from datetime import datetime, timezone
from contextlib import closing
import json
import math
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import time
import uuid

HIDDEN_RECEIPTS_TABLE = 'history_hidden_connector_receipts'
NOTIFICATION_MARKER = 'notifications-cleared.json'
_TOMBSTONE = b'{"type":"cleared"}'


def _summary():
    return {'complete': True, 'notifications': 0, 'events': 0, 'files': 0,
            'files_removed': [], 'errors': [], 'cleared_at': None,
            'retained': 'Timer, prenotazioni, ricevute operative, tentativi e bozze conservati.'}


def _checked(path, directory=False):
    """Reject reparse points, symlinks and shared file inodes before mutation."""
    try:info=path.lstat()
    except FileNotFoundError:return None
    if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0) & getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',0):
        raise ValueError(f'Percorso collegato non modificato: {path.name}')
    if directory:
        if not stat.S_ISDIR(info.st_mode):raise ValueError(f'Cartella non valida: {path.name}')
    elif not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:
        raise ValueError(f'File condiviso o non regolare non modificato: {path.name}')
    return info


def _logs(root):
    root=Path(root).absolute()
    for ancestor in root.parents:_checked(ancestor,directory=True)
    if _checked(root,directory=True) is None:return None
    logs=root/'logs'
    if _checked(logs,directory=True) is None:return None
    if not logs.resolve().is_relative_to(root.resolve()):raise ValueError('Cartella registri esterna al profilo')
    return logs


def _file(logs,name):
    if Path(name).name!=name:raise ValueError('Nome registro non valido')
    path=logs/name
    if path.parent.resolve()!=logs.resolve():raise ValueError('Registro esterno al profilo')
    return path,_checked(path)


def _same_open_file(stream,initial):
    current=os.fstat(stream.fileno())
    if current.st_nlink!=1 or (current.st_dev,current.st_ino)!=(initial.st_dev,initial.st_ino):
        raise ValueError('Il registro è cambiato durante la pulizia')


def _redact(path,initial,predicate,deadline=None):
    """Touch complete matching lines in the initial snapshot, never new appends."""
    if initial is None:return 0
    count=0;deadline=min(time.monotonic()+20,deadline or float('inf'))
    flags=os.O_RDWR|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0)
    with os.fdopen(os.open(path,flags),'r+b',buffering=0) as stream:
        _same_open_file(stream,initial)
        while stream.tell()<initial.st_size:
            if time.monotonic()>deadline:raise TimeoutError('Pulizia del registro incompleta: limite di 20 secondi')
            start=stream.tell();line=stream.readline(min(initial.st_size-start,4*1024*1024))
            if len(line)==4*1024*1024 and not line.endswith(b'\n'):
                raise ValueError('Registro con riga troppo grande: pulizia interrotta senza cambiare gli offset')
            if not line or not line.endswith(b'\n'):break
            try:record=json.loads(line.decode('utf-8-sig'))
            except (UnicodeError,ValueError):continue
            if not isinstance(record,dict) or not predicate(record):continue
            ending=b'\r\n' if line.endswith(b'\r\n') else b'\n'
            length=len(line)-len(ending)
            if length<len(_TOMBSTONE):continue
            stream.seek(start);stream.write(_TOMBSTONE+b' '*(length-len(_TOMBSTONE))+ending)
            count+=1
        os.fsync(stream.fileno())
    return count


def _stamp(value):
    if isinstance(value,(int,float)) and not isinstance(value,bool):return float(value) if math.isfinite(value) else None
    if isinstance(value,str):
        try:
            parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
            return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).timestamp()
        except (ValueError,OverflowError):pass
    return None


def _write_marker(logs,cutoff):
    path,_=_file(logs,NOTIFICATION_MARKER)
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=logs,prefix='.notifications-cleared-',suffix='.tmp',delete=False) as stream:
            temporary=Path(stream.name);json.dump({'cleared_at':cutoff},stream);stream.flush();os.fsync(stream.fileno())
        _file(logs,NOTIFICATION_MARKER)
        os.replace(temporary,path)
    finally:
        if temporary is not None and temporary.exists():temporary.unlink()


def clear_notifications(root):
    """Clear all notification payloads up to cleared_at, preserving queue offsets.

    Keep the GUI/backend deduplicators intact. The GUI must ignore Windows
    notifications created at/before the returned and persisted UTC cutoff on
    later startups. No Windows notification or external file is manipulated.
    """
    result=_summary();cutoff=time.time()
    try:
        logs=_logs(root)
        if logs is None:return result
        marker,marker_info=_file(logs,NOTIFICATION_MARKER)
        if marker_info:
            metadata=json.loads(marker.read_text(encoding='utf-8'))
            old=_stamp(metadata.get('cleared_at')) if isinstance(metadata,dict) else None
            if old is None:raise ValueError('Confine della cronologia notifiche non valido: file conservato')
            cutoff=max(cutoff,old)
        path,info=_file(logs,'events.jsonl')
        def eligible(record):
            if record.get('type')!='notification':return False
            timestamps=[_stamp(record.get(key)) for key in ('created','observed_at')]
            return not any(value is not None and value>cutoff for value in timestamps)
        result['notifications']=_redact(path,info,eligible)
        _write_marker(logs,cutoff);result['cleared_at']=cutoff
    except (OSError,ValueError,sqlite3.Error,TimeoutError) as exc:
        result['complete']=False;result['errors'].append(str(exc))
    return result


def _table(db,name):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone() is not None


def clear_delivery_history(root):
    """Delete all display events and only confirmed, standalone receipt exports.

    SQL delivery ledgers are never deleted. Exact confirmed tokens are marked
    as already cleared for the legacy history importer. Pending tokens are not
    marked, so their later genuine confirmations remain visible and cannot be
    mistaken for deleted attempts. No caller-provided path from a log is used.
    """
    result=_summary();confirmed=set();deadline=time.monotonic()+40
    try:
        logs=_logs(root)
        if logs is None:return result
        database,info=_file(logs,'messages.sqlite3')
        for suffix in ('-wal','-shm','-journal'):_file(logs,'messages.sqlite3'+suffix)
        if info is not None:
            with closing(sqlite3.connect(database.as_uri()+'?mode=rw',uri=True,timeout=1)) as db:
                sql_deadline=time.monotonic()+10
                db.set_progress_handler(lambda:int(time.monotonic()>sql_deadline),1000)
                db.execute('BEGIN IMMEDIATE')
                if _table(db,'connector_deliveries'):
                    confirmed={row[0] for row in db.execute('SELECT token FROM connector_deliveries WHERE confirmed_at IS NOT NULL AND receipt IS NOT NULL') if isinstance(row[0],str) and row[0]}
                    db.execute(f'CREATE TABLE IF NOT EXISTS {HIDDEN_RECEIPTS_TABLE} (token TEXT PRIMARY KEY NOT NULL)')
                    db.execute(f"INSERT OR IGNORE INTO {HIDDEN_RECEIPTS_TABLE} SELECT token FROM connector_deliveries WHERE token IS NOT NULL AND token!='' AND confirmed_at IS NOT NULL AND receipt IS NOT NULL")
                removed=0
                if _table(db,'message_events'):
                    removed=db.execute('SELECT COUNT(*) FROM message_events').fetchone()[0]
                    db.execute('DELETE FROM message_events')
                db.commit()
                result['events']=removed
        for token in confirmed:
            try:
                if not isinstance(token,str) or str(uuid.UUID(token))!=token:continue
            except ValueError:continue
            for prefix in ('codex-receipt-','codex-local-receipt-'):
                if time.monotonic()>deadline:raise TimeoutError('Pulizia cronologia incompleta: limite di 40 secondi')
                try:
                    path,info=_file(logs,prefix+token+'.json')
                    if info is not None:
                        _checked(path);path.unlink();result['files_removed'].append(path.name)
                except (OSError,ValueError) as exc:
                    result['complete']=False;result['errors'].append(str(exc))
        audit,info=_file(logs,'codex-delivery-events.jsonl')
        result['audit_events']=_redact(audit,info,lambda record:isinstance(record.get('token'),str) and record['token'] in confirmed,deadline)
    except (OSError,ValueError,sqlite3.Error,TimeoutError) as exc:
        result['complete']=False;result['errors'].append(str(exc))
    result['files']=len(result['files_removed'])
    return result
