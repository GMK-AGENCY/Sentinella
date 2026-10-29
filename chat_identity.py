"""Stable recipient bindings. Metadata is read-only; timers and drafts are untouched."""
import json
import hashlib
import os
from pathlib import Path
import sqlite3
from contextlib import contextmanager, closing

from chat_catalog import codex_metadata, claude_metadata, project_name


def key(job):
    return job.get('app'), job.get('thread_id') if job.get('app')=='codex' else job.get('session_id')


def resolve(job):
    """An existing ID never falls back to a matching name."""
    app,identifier=key(job)
    if app=='codex':
        rows=codex_metadata(set() if identifier else {job['title']},thread_ids=[identifier] if identifier else ())
        field='thread_id'
    elif app=='claude':
        rows=claude_metadata(set() if identifier else {job['title']},session_ids=[identifier] if identifier else ())
        field='session_id'
    else:raise ValueError('Applicazione del destinatario non valida')
    if identifier:rows=[row for row in rows if row.get(field)==identifier]
    else:
        exact=[row for row in rows if row.get('project','').casefold()==project_name(job.get('project') or job.get('group')).casefold()]
        rows=exact or rows
    if len(rows)!=1:
        raise ValueError('ID della chat non disponibile o non univoco: nessun destinatario alternativo')
    row=rows[0]
    if not row.get(field):raise ValueError('ID della chat non verificato')
    return {**job,field:row[field],'session_id':row.get('session_id') or row.get('thread_id'),
            'title':row['title'],'project':row.get('project',job.get('project'))}


@contextmanager
def config_lock(path):
    """All Sentinella writers share this short, non-blocking file lock."""
    import msvcrt
    lock=Path(path).with_suffix('.lock').open('a+b')
    try:
        if not lock.tell():lock.write(b'0');lock.flush()
        lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        try:yield
        finally:
            lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)
    finally:lock.close()


def _owned_binding(job,root):
    """Prefer this job/version's existing ownership proof to an old title."""
    path=root/'logs/messages.sqlite3'
    if job.get('app')!='claude' or job.get('session_id') or not path.exists():return job
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.2)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='claude_timer_deliveries'").fetchone():return job
        row=db.execute('SELECT identity FROM claude_timer_deliveries WHERE job_id=? AND version=? ORDER BY prepared_at DESC LIMIT 1',
                       (job['id'],job['version'])).fetchone()
    if not row:return job
    identity=json.loads(row[0]);index=Path(identity['index'])
    metadata=json.loads(index.read_text(encoding='utf-8'))
    if metadata.get('isArchived') or metadata.get('cliSessionId')!=identity['session']:
        raise ValueError('La sessione della prenotazione è cambiata: nessuna nuova associazione')
    return {**job,'session_id':index.stem.removeprefix('local_')}


def refresh_saved(root, resolver=resolve):
    """Bind legacy jobs and refresh display names, preserving scheduling fields."""
    root=Path(root).resolve();path=root/'messages-config.json'
    if not path.exists():return {'updated':[],'unresolved':[]}
    report={'updated':[],'unresolved':[]}
    with config_lock(path):
        original=path.read_bytes();data=json.loads(original);changed=False
        for job in data.get('jobs',[]):
            try:
                bound=resolver(_owned_binding(job,root))
                updates={name:bound[name] for name in ('thread_id','session_id','title','project') if name in bound}
                if any(job.get(name)!=value for name,value in updates.items()):
                    job.update(updates);changed=True;report['updated'].append(job['id'])
            except (OSError,ValueError,KeyError,sqlite3.Error) as exc:
                report['unresolved'].append({'job_id':job.get('id'),'reason':str(exc)})
        if changed:
            backup=root/'backups'/('identities-'+hashlib.sha256(original).hexdigest()+'.json')
            backup.parent.mkdir(exist_ok=True)
            try:
                with backup.open('xb') as stream:stream.write(original)
            except FileExistsError:pass
            temporary=path.with_suffix('.identity.tmp')
            temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            os.replace(temporary,path)
    return report
