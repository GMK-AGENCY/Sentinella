"""Explicitly acknowledge legacy uncertainty and authorize only future slots.

This action belongs behind the GUI's confirmation dialog. It never proves a
delivery, rewrites an old attempt, retries a draft, or changes configuration.
The append-only resolution is separate from receipts, so the old uncertainty
remains auditable even after the user elects to resume future scheduling.
"""
import json
import math
from pathlib import Path, PureWindowsPath
import sqlite3
from types import SimpleNamespace
import uuid

from chat_identity import config_lock


class RecoveryConflict(ValueError):
    """The confirmed popup no longer describes the target's current state."""


def ensure_schema(db):
    """Can run inside an existing transaction; never commits the caller's work."""
    db.execute('''CREATE TABLE IF NOT EXISTS legacy_delivery_resolutions (
        token TEXT PRIMARY KEY, app TEXT NOT NULL, target_id TEXT NOT NULL,
        job_id TEXT NOT NULL, version TEXT NOT NULL, at REAL NOT NULL,
        old_due REAL NOT NULL, next_due REAL, kind TEXT NOT NULL
        CHECK(kind IN ('future_only','archived_unconfirmed')))''')
    for operation in ('UPDATE', 'DELETE'):
        db.execute(f'''CREATE TRIGGER IF NOT EXISTS legacy_resolution_no_{operation.lower()}
            BEFORE {operation} ON legacy_delivery_resolutions BEGIN
            SELECT RAISE(ABORT,'Legacy resolutions are append-only'); END''')


def _tables(db):
    return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _rows(db, sql, args=()):
    cursor = db.execute(sql, args)
    fields = [column[0] for column in cursor.description]
    return [dict(zip(fields, row)) for row in cursor]


def _uuid(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise RecoveryConflict('Identità della chat o della prenotazione non valida')
    return value


def _target(job):
    app = job.get('app')
    if app == 'codex':
        logical = _uuid(job.get('thread_id'))
        return app, logical, logical
    if app != 'claude':
        raise RecoveryConflict('Applicazione della chat non valida')
    logical = _uuid(job.get('session_id'))
    from claude_delivery import locate
    try:
        identity = locate(job)
        runtime = _uuid(identity.get('session'))
        index_id = PureWindowsPath(identity['index']).stem.removeprefix('local_')
        if index_id != logical:
            raise RecoveryConflict('Identità Claude non corrispondente alla programmazione')
        return app, logical, runtime
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise RecoveryConflict('Identità Claude non univoca: nessuna ripartenza') from exc


def resolution_tokens(store, app, target_id):
    """Read-only helper for journal quarantine checks; these are NOT receipts."""
    if 'legacy_delivery_resolutions' not in _tables(store.db):
        return set()
    return {row[0] for row in store.db.execute(
        'SELECT token FROM legacy_delivery_resolutions WHERE app=? AND target_id=?', (app, target_id))}


def _pending(store, job, target):
    db = store.db
    app, logical, runtime = target
    tables = _tables(db)
    resolved = resolution_tokens(store, app, runtime)
    result = {}
    if 'connector_deliveries' in tables:
        cancelled = set()
        if 'local_codex_deliveries' in tables:
            cancelled = {row[0] for row in db.execute(
                "SELECT token FROM local_codex_deliveries WHERE stage='cancelled_before_send'")}
        selector = '(job_id=? OR thread_id=?)' if app == 'codex' else 'job_id=?'
        arguments = (job['id'], runtime) if app == 'codex' else (job['id'],)
        for row in _rows(db, 'SELECT token,job_id,version,thread_id,reserved_at FROM connector_deliveries '
                         'WHERE confirmed_at IS NULL AND ' + selector, arguments):
            if row['token'] in cancelled or row['token'] in resolved:
                continue
            relevant = row['job_id'] == job['id'] or app == 'codex' and row['thread_id'] == runtime
            if not relevant:
                continue
            if app != 'codex' or _uuid(row['thread_id']) != runtime:
                raise RecoveryConflict('Una prenotazione precedente del job riguarda un altro destinatario')
            token = _uuid(row['token'])
            result[token] = {'token': token, 'app': app, 'target_id': runtime,
                             'job_id': row['job_id'], 'version': row['version'], 'at': row['reserved_at']}
    if 'claude_timer_deliveries' in tables:
        for row in _rows(db, "SELECT token,job_id,version,identity,prepared_at FROM claude_timer_deliveries "
                         "WHERE stage IN ('prepared','sending')"):
            if row['token'] in resolved:
                continue
            if app != 'claude':
                if row['job_id'] == job['id']:
                    raise RecoveryConflict('Una prenotazione precedente del job riguarda un’altra applicazione')
                continue
            try:
                identity = json.loads(row['identity'])
                old_runtime = _uuid(identity.get('session'))
                old_logical = _uuid(PureWindowsPath(identity['index']).stem.removeprefix('local_'))
            except (KeyError, TypeError, ValueError) as exc:
                raise RecoveryConflict('Prenotazione Claude senza identità verificabile: ripartenza bloccata') from exc
            if row['job_id'] == job['id'] and (old_runtime != runtime or old_logical != logical):
                raise RecoveryConflict('Il destinatario della prenotazione Claude è cambiato')
            if old_runtime != runtime:
                continue
            token = _uuid(row['token'])
            if token in result:
                raise RecoveryConflict('Identificativo della prenotazione non univoco')
            result[token] = {'token': token, 'app': app, 'target_id': runtime,
                             'job_id': row['job_id'], 'version': row['version'], 'at': row['prepared_at']}
    return [result[key] for key in sorted(result)]


def list_legacy_pending(store, job):
    """Read-only popup snapshot, including all aliases of the exact target ID."""
    return _pending(store, job, _target(job))


def _future_due(old_due, interval, now):
    if old_due > now:
        return old_due
    due = old_due + (math.floor((now - old_due) / interval) + 1) * interval
    return due if due > now else due + interval


def _check_other_due_jobs(db, jobs, selected, target, now):
    # A target-wide acknowledgement must not release an overdue alias which
    # the popup did not authorize us to reschedule. Preserve its timer/config.
    for other in jobs:
        if other.get('id') == selected['id'] or not other.get('enabled') or other.get('app') != target[0]:
            continue
        if _target(other)[2] != target[2]:
            continue
        timers = _rows(db, 'SELECT version,due FROM timers WHERE id=?', (other.get('id'),))
        if (len(timers) != 1 or timers[0]['version'] != other.get('version')
                or not isinstance(timers[0]['due'], (int, float))
                or not math.isfinite(timers[0]['due']) or timers[0]['due'] <= now):
            raise RecoveryConflict('Un’altra programmazione attiva dello stesso destinatario è scaduta: '
                                   'mettila in pausa prima di ripartire dai soli invii futuri')


def apply_resolution(root, job_id, version, expected_tokens, now):
    """Apply only the exact token set explicitly confirmed by the GUI.

    Configuration lock + SQLite writer lock fence edits and new reservations.
    A repeated call with an old popup fails rather than moving the timer again.
    No sender or reconciliation method is called from this function.
    """
    if isinstance(expected_tokens, (str, bytes)):
        raise RecoveryConflict('Elenco delle prenotazioni non valido')
    supplied = list(expected_tokens)
    expected = {_uuid(token) for token in supplied}
    if not expected or len(expected) != len(supplied) or not math.isfinite(now):
        raise RecoveryConflict('Conferma delle prenotazioni non valida')
    root = Path(root).resolve()
    config = root / 'messages-config.json'
    with config_lock(config):
        jobs = json.loads(config.read_text(encoding='utf-8')).get('jobs', [])
        matches = [job for job in jobs if job.get('id') == job_id]
        if len(matches) != 1 or matches[0].get('version') != version:
            raise RecoveryConflict('Programmazione modificata: riapri la conferma')
        job = matches[0]
        target = _target(job)
        db = sqlite3.connect((root / 'logs/messages.sqlite3').as_uri() + '?mode=rw', uri=True, timeout=.5)
        try:
            db.execute('BEGIN IMMEDIATE')
            ensure_schema(db)
            store = SimpleNamespace(db=db)
            pending = _pending(store, job, target)
            if {row['token'] for row in pending} != expected:
                raise RecoveryConflict('Le prenotazioni sono cambiate: riapri la conferma')
            if _target(job) != target:
                raise RecoveryConflict('Identità della chat cambiata durante la conferma')
            app, logical, runtime = target
            _check_other_due_jobs(db, jobs, job, target, now)
            if 'native_attempts' in _tables(db) and db.execute('''SELECT 1 FROM native_attempts
                WHERE state NOT IN ('received','cancelled_before_dispatch','rejected_before_dispatch')
                AND (job_id=? OR (app=? AND (runtime_id=? OR target_id=?))) LIMIT 1''',
                (job_id, app, runtime, logical)).fetchone():
                raise RecoveryConflict('Esiste già un invio nativo aperto: nessuna ripartenza')
            timers = _rows(db, 'SELECT version,due,phase,last_sent FROM timers WHERE id=?', (job_id,))
            if len(timers) != 1 or timers[0]['version'] != version:
                raise RecoveryConflict('Timer della programmazione non corrispondente')
            timer = timers[0]
            if timer['phase'] not in {'idle', 'drafted', 'uncertain'}:
                raise RecoveryConflict('Stato del timer non risolvibile con questa azione')
            old_due = float(timer['due']); interval = float(job['interval_seconds'])
            if not math.isfinite(old_due) or not math.isfinite(interval) or not 60 <= interval <= 7 * 86400:
                raise RecoveryConflict('Scadenza o intervallo non validi')
            repeat = job.get('repeat', True)
            if not isinstance(repeat, bool):
                raise RecoveryConflict('Ripetizione della programmazione non valida')
            next_due = _future_due(old_due, interval, now) if repeat else None
            if next_due is not None and not math.isfinite(next_due):
                raise RecoveryConflict('Prossima scadenza non valida')
            kind = 'future_only' if repeat else 'archived_unconfirmed'
            detail = ('Ripartenza autorizzata dai soli invii futuri; il tentativo precedente resta non confermato'
                      if repeat else 'Programmazione singola archiviata senza confermare né ripetere il vecchio invio')
            for token in sorted(expected):
                db.execute('INSERT INTO legacy_delivery_resolutions VALUES(?,?,?,?,?,?,?,?,?)',
                           (token, app, runtime, job_id, version, now, old_due, next_due, kind))
            db.execute('UPDATE timers SET phase=?,due=?,detail=? WHERE id=? AND version=?',
                       ('idle' if repeat else 'finished', next_due if repeat else old_due, detail, job_id, version))
            if 'message_failures' in _tables(db):
                db.execute('UPDATE message_failures SET resolved_at=? WHERE job_id=? AND version=? AND resolved_at IS NULL',
                           (now,job_id,version))
            if 'message_retry' in _tables(db):
                db.execute('DELETE FROM message_retry WHERE job_id=? AND version=?',(job_id,version))
            db.execute('INSERT INTO message_events(at,job_id,title,event,detail) VALUES(?,?,?,?,?)',
                       (now, job_id, job.get('title', ''), 'Ripartenza futura autorizzata' if repeat
                        else 'Archiviata senza conferma', detail))
            db.commit()
            return {'action': 'future_only' if repeat else 'archived_unconfirmed',
                    'tokens': sorted(expected), 'app': app, 'target_id': runtime,
                    'old_due': old_due, 'next_due': next_due, 'last_sent': timer['last_sent']}
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()
