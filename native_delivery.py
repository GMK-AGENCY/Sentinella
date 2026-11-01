"""A single native dispatcher, durable intent before effect, read-only recovery.

No queue disappearance/timeout means 'not sent'. In that case only positive
native receipts can finish an attempt; a later process never sends it again.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import time
import sqlite3


def work_due(jobs, database, now=None):
    """Read-only cheap scheduling; pending receipts survive disabled/deleted jobs."""
    now=time.time() if now is None else now
    try:
        db=sqlite3.connect(Path(database).as_uri()+'?mode=ro',uri=True,timeout=.2)
        try:
            names={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'native_attempts' in names and db.execute("SELECT 1 FROM native_attempts WHERE state IN ('reserved','dispatch_intent','accepted','unknown') AND probe_at<=? LIMIT 1",(now,)).fetchone():return True
            for job in jobs:
                if not job.get('enabled'):continue
                row=db.execute('SELECT version,phase,due FROM timers WHERE id=?',(job['id'],)).fetchone()
                if not row or row[0]!=job['version'] or row[1]=='idle' and row[2]<=now:return True
            return False
        finally:db.close()
    except (OSError,ValueError,sqlite3.Error):return any(j.get('enabled') for j in jobs)


def session_authority(root, run_id, now=None):
    """Live GUI ancestry plus its current run and explicit exit intent.

    A stale heartbeat or a reused PID alone cannot authorize effects. A GUI
    restart has a new run ID; its old workers cannot inherit that authority.
    """
    import psutil
    from runtime_paths import frozen
    import sys
    now=time.time() if now is None else now
    try:
        logs=Path(root)/'logs'
        heartbeat=json.loads((logs/'gui-heartbeat.json').read_text(encoding='utf-8'))
        if not run_id or heartbeat.get('run_id')!=run_id or not 0<=now-heartbeat['updated_at']<45:return False
        exit_path=logs/'exit-intent.json'
        if exit_path.exists() and json.loads(exit_path.read_text(encoding='utf-8')).get('run_id')==run_id:return False
        parents={p.pid:p for p in psutil.Process().parents()}
        gui=parents.get(heartbeat['pid'])
        if gui is None or gui.create_time()>heartbeat['updated_at'] or not gui.is_running():return False
        args=gui.cmdline()
        return (Path(gui.exe()).resolve()==Path(sys.executable).resolve() and '--process' in args and 'notification_watch' in args
                if frozen() else any(Path(arg).name=='notification_watch.py' for arg in args))
    except (OSError,ValueError,TypeError,KeyError,psutil.Error):return False

from automation_policy import action_block_reason
from delivery_journal import Journal


@contextmanager
def worker_lock(path):
    """OS-owned, nonblocking lock: no lease expiry or takeover of a live worker."""
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+b') as stream:
        stream.seek(0)
        locked=False
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
            locked=True
        except OSError:pass
        if locked and os.fstat(stream.fileno()).st_size==0:
            stream.write(b'0');stream.flush()
        try:yield locked
        finally:
            if locked:
                stream.seek(0)
                if os.name=='nt':msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
                else:fcntl.flock(stream,fcntl.LOCK_UN)


def block(job, config):
    if config.get('paused'):return 'Sentinella in pausa'
    if not job.get('enabled'):return 'Programmazione in pausa'
    return None if job.get('send_when_present') else action_block_reason(config)


def same_authorization(original, current):
    fields=('id','version','app','thread_id','session_id','message','interval_seconds',
            'repeat','send_when_active','send_when_present','transport')
    return bool(current and all(original.get(k)==current.get(k) for k in fields))


def reconcile(attempt, journal, config, root):
    from native_receipts import claude_receipt
    from native_transports import CodexTransport
    if attempt['app']=='claude':
        receipt,cursor=claude_receipt(attempt)
        binding=json.loads(attempt['binding_json']);binding['history_offset']=cursor
        detail='Ricezione non ancora verificata; nessun reinvio'
    else:
        client=CodexTransport(json.loads(attempt['job_json']),config,root,readonly=True,
            binding=json.loads(attempt['binding_json']))
        try:receipt,binding,detail=client.reconcile(attempt)
        finally:client.close()
    now=time.time()
    if receipt:
        journal.confirm(attempt['id'],receipt,now)
        return {'action':'confirmed','attempt':attempt['id'],'job':attempt['job_id'],'receipt':receipt['physical_id']}
    journal.binding(attempt['id'],binding)
    if attempt['state']!='reserved':
        journal.transition(attempt['id'],'accepted' if detail.startswith('Accodato') else 'unknown',detail,now)
    journal.defer_probe(attempt['id'],now)
    return {'action':'awaiting_receipt','attempt':attempt['id'],'job':attempt['job_id']}


def cycle(store, root, read_config, read_jobs, factory=None, now=None, recover=None, authorized=lambda:True, reconcile_only=False):
    from native_transports import transport,NotDispatched
    factory=factory or transport
    recover=recover or reconcile
    clock=time.time if now is None else lambda:now
    at=clock()
    journal=Journal(store)
    results=[]
    # Reconciliation is independent of pause, eligibility, version or deletion.
    # Bound each cycle and rotate probe_at even on errors, avoiding starvation.
    deadline=time.monotonic()+24
    for attempt in journal.pending(at)[:2]:
        if attempt['state']=='reserved':
            journal.transition(attempt['id'],'cancelled_before_dispatch',
                'Preparazione interrotta prima dell’invio; scadenza conservata',clock())
            continue
        try:results.append(recover(attempt,journal,read_config(),root))
        except Exception as exc:
            journal.transition(attempt['id'],'unknown',str(exc)[:1000],time.time())
            results.append({'action':'awaiting_receipt','job':attempt['job_id'],'error':str(exc)[:1000]})
        journal.defer_probe(attempt['id'],time.time())
    jobs=[] if reconcile_only else read_jobs()
    for job in sorted(jobs,key=lambda j:j.get('activated_at',at)):
        if time.monotonic()>=deadline:break
        if not job.get('enabled'):continue
        config=read_config()
        if block(job,config) or not authorized():continue
        state=store.prepare(job,clock())
        if state['phase']!='idle' or state['due']>clock() or store.retry_pending(job,clock()):continue
        client=None;attempt=None
        try:
            client=factory(job,config,root)
            attempt=journal.reserve(job,client.binding,clock())
            if not attempt:continue
            client.prepare(attempt)
            journal.binding(attempt['id'],client.binding)
            def guard():
                current=next((j for j in read_jobs() if j['id']==job['id']),None)
                return same_authorization(job,current) and not block(current,read_config()) and authorized()
            client.guard=guard
            if not guard():
                journal.transition(attempt['id'],'cancelled_before_dispatch','Pausa o modifica prima dell’invio',time.time())
                return {'action':'paused','job':job['id']}
            if not journal.transition(attempt['id'],'dispatch_intent','Consegna nativa iniziata',time.time()):
                return {'action':'busy'}
            attempt=journal.get(attempt['id'])
            # The intent is already durable; no exception beyond here releases it.
            reply=client.dispatch(attempt)
            journal.transition(attempt['id'],'accepted' if reply.get('accepted') else 'unknown',
                reply['detail'],time.time(),reply.get('transport_id'))
            store.recover(job,time.time())
            return {'action':'accepted' if reply.get('accepted') else 'awaiting_receipt',
                    'job':job['id'],'attempt':attempt['id'],'reason':reply['detail']}
        except Exception as exc:
            error=f'{type(exc).__name__}: {exc}'[:1000]
            persisted=journal.get(attempt['id']) if attempt else None
            if isinstance(exc,NotDispatched) and persisted and persisted['state']=='dispatch_intent':
                journal.transition(attempt['id'],'rejected_before_dispatch',error,time.time())
            elif persisted and persisted['state']!='reserved':
                journal.transition(attempt['id'],'unknown',error,time.time())
            else:
                if attempt:journal.transition(attempt['id'],'cancelled_before_dispatch',error,time.time())
                store.fail(job,error,clock())
            results.append({'action':'error','job':job['id'],'error':error})
        finally:
            if client:client.close()
    return {'action':'checked' if results else 'idle','results':results}


def run(root, read_config=None, read_jobs=None, reconcile_only=False):
    from message_engine import TimerStore
    from automation_policy import load_config
    from message_engine import load_jobs
    with worker_lock(Path(root)/'logs/native-delivery.lock') as acquired:
        if not acquired:return {'action':'busy'}
        store=TimerStore(Path(root)/'logs/messages.sqlite3')
        run_id=os.environ.get('SENTINELLA_RUN_ID')
        try:return cycle(store,root,read_config or load_config,read_jobs or load_jobs,
            authorized=lambda:session_authority(root,run_id),reconcile_only=reconcile_only)
        finally:store.db.close()
