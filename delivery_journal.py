"""Transactional delivery intents and monotone receipts, independent of the UI.

An intent survives worker failures. Only a positive, uniquely assigned native
receipt advances the timer. Neither a timeout nor a missing row permits resend.
"""
import hashlib
import json
import sqlite3
import uuid

OPEN = ('reserved', 'dispatch_intent', 'accepted', 'unknown')


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


class Journal:
    def __init__(self, store):
        self.store, self.db = store, store.db
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS native_attempts (
                id TEXT PRIMARY KEY, job_id TEXT NOT NULL, version TEXT NOT NULL,
                app TEXT NOT NULL, target_id TEXT NOT NULL, runtime_id TEXT NOT NULL, due REAL NOT NULL,
                job_json TEXT NOT NULL, binding_json TEXT NOT NULL,
                message_hash TEXT NOT NULL, created REAL NOT NULL, dispatched REAL,
                state TEXT NOT NULL, transport_id TEXT, receipt_id TEXT,
                detail TEXT NOT NULL DEFAULT '', probe_at REAL NOT NULL DEFAULT 0);
            CREATE UNIQUE INDEX IF NOT EXISTS native_effective_slot
                ON native_attempts(job_id,version,due)
                WHERE state NOT IN ('cancelled_before_dispatch','rejected_before_dispatch');
            CREATE UNIQUE INDEX IF NOT EXISTS native_open_target
                ON native_attempts(app,runtime_id)
                WHERE state IN ('reserved','dispatch_intent','accepted','unknown');
            CREATE UNIQUE INDEX IF NOT EXISTS native_open_logical_target
                ON native_attempts(app,target_id)
                WHERE state IN ('reserved','dispatch_intent','accepted','unknown');
            CREATE TABLE IF NOT EXISTS native_receipts (
                app TEXT NOT NULL, target_id TEXT NOT NULL, physical_id TEXT NOT NULL,
                attempt_id TEXT UNIQUE NOT NULL, received REAL NOT NULL,
                evidence_json TEXT NOT NULL,
                PRIMARY KEY(app,target_id,physical_id));
            CREATE TABLE IF NOT EXISTS native_delivery_events (
                seq INTEGER PRIMARY KEY, attempt_id TEXT NOT NULL,
                at REAL NOT NULL, state TEXT NOT NULL, detail TEXT NOT NULL);
        ''')

    def _event(self, token, state, detail, now):
        self.db.execute('INSERT INTO native_delivery_events(attempt_id,at,state,detail) VALUES(?,?,?,?)',
                        (token, now, state, detail))

    def get(self, token):
        row = self.db.execute('SELECT * FROM native_attempts WHERE id=?', (token,)).fetchone()
        return dict(row) if row else None

    def pending(self, now):
        # Independent of current configuration: also reconcile paused, deleted,
        # renamed and superseded jobs. Poll order cannot starve another target.
        return [dict(r) for r in self.db.execute('''SELECT * FROM native_attempts
            WHERE state IN ('reserved','dispatch_intent','accepted','unknown') AND probe_at<=?
            ORDER BY probe_at,created,id''', (now,))]

    def legacy_pending(self, job, binding):
        from delivery_recovery import resolution_tokens
        resolved=resolution_tokens(self.store,job['app'],binding['runtime_id'])
        names = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'connector_deliveries' in names:
            columns = {r[1] for r in self.db.execute('PRAGMA table_info(connector_deliveries)')}
            same_target = ' OR thread_id=?' if job['app']=='codex' and 'thread_id' in columns else ''
            query = 'SELECT token FROM connector_deliveries WHERE (job_id=?'+same_target+') AND confirmed_at IS NULL'
            if 'local_codex_deliveries' in names:
                query += " AND token NOT IN (SELECT token FROM local_codex_deliveries WHERE stage='cancelled_before_send')"
            args = (job['id'],binding['runtime_id']) if same_target else (job['id'],)
            if any(row[0] not in resolved for row in self.db.execute(query,args)):
                return True
        if 'claude_timer_deliveries' in names and job['app']=='claude':
            for row in self.db.execute("SELECT * FROM claude_timer_deliveries WHERE stage IN ('prepared','sending')"):
                if 'token' in row.keys() and row['token'] in resolved:continue
                if row['job_id']==job['id']: return True
                try: identity=json.loads(row['identity'])
                except (ValueError,TypeError): return True  # Unmapped legacy uncertainty cannot be bypassed.
                if identity.get('session')==binding['runtime_id']: return True
        return False

    def reserve(self, job, binding, now):
        target = job.get('thread_id') if job['app'] == 'codex' else job.get('session_id')
        if not target or not binding.get('runtime_id') or binding.get('logical_id') != target or binding.get('app') != job['app']:
            raise ValueError('Identità nativa non verificata')
        token = str(uuid.uuid4())
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row = self.db.execute('SELECT * FROM timers WHERE id=? AND version=?',
                                  (job['id'], job['version'])).fetchone()
            if (not row or row['phase'] != 'idle' or row['due'] > now or self.legacy_pending(job,binding)
                    or self.db.execute("SELECT 1 FROM native_attempts WHERE app=? AND (runtime_id=? OR target_id=?) AND state IN ('reserved','dispatch_intent','accepted','unknown')", (job['app'], binding['runtime_id'],target)).fetchone()):
                self.db.rollback()
                return None
            self.db.execute('''INSERT INTO native_attempts
                (id,job_id,version,app,target_id,runtime_id,due,job_json,binding_json,message_hash,created,state)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,'reserved')''',
                (token,job['id'],job['version'],job['app'],target,binding['runtime_id'],row['due'],json.dumps(job),
                 json.dumps(binding),digest(job['message']),now))
            self.db.execute("UPDATE timers SET phase='uncertain',detail=? WHERE id=? AND version=?",
                            ('Invio prenotato; nessuna ricezione ancora verificata',job['id'],job['version']))
            self._event(token,'reserved','Prenotazione atomica prima di ogni effetto',now)
            self.db.commit()
        except sqlite3.IntegrityError as exc:
            self.db.rollback()
            if exc.sqlite_errorcode in {sqlite3.SQLITE_CONSTRAINT_UNIQUE,sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY}:
                return None
            raise
        except BaseException:
            self.db.rollback()
            raise
        return self.get(token)

    def transition(self, token, state, detail, now, transport_id=None):
        allowed = {
            'dispatch_intent': {'reserved'},
            'accepted': {'dispatch_intent','unknown','accepted'},
            'unknown': {'dispatch_intent','accepted','unknown'},
            'cancelled_before_dispatch': {'reserved'},
            'rejected_before_dispatch': {'dispatch_intent'},
        }
        if state not in allowed:
            raise ValueError('Transizione non valida')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row = self.get(token)
            if not row or row['state'] not in allowed[state]:
                self.db.rollback()
                return False
            self.db.execute('''UPDATE native_attempts SET state=?,detail=?,
                transport_id=COALESCE(?,transport_id),probe_at=?,
                dispatched=CASE WHEN ?='dispatch_intent' THEN ? ELSE dispatched END WHERE id=?''',
                (state,detail,transport_id,now+5,state,now,token))
            phase = 'idle' if state in {'cancelled_before_dispatch','rejected_before_dispatch'} else 'uncertain'
            self.db.execute('UPDATE timers SET phase=?,detail=? WHERE id=? AND version=?',
                            (phase,detail,row['job_id'],row['version']))
            self._event(token,state,detail,now)
            self.db.commit()
            return True
        except BaseException:
            self.db.rollback()
            raise

    def defer_probe(self, token, now):
        self.db.execute('UPDATE native_attempts SET probe_at=? WHERE id=?', (now+5,token))
        self.db.commit()

    def binding(self, token, binding):
        row=self.get(token)
        old=json.loads(row['binding_json'])
        if any(binding.get(k)!=old.get(k) for k in ('app','logical_id','runtime_id')):
            raise ValueError('Identità della prenotazione immutabile')
        self.db.execute('UPDATE native_attempts SET binding_json=? WHERE id=?', (json.dumps(binding),token))
        self.db.commit()

    def confirm(self, token, receipt, now):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row = self.get(token)
            if not row:
                raise ValueError('Prenotazione inesistente')
            if row['state'] == 'received':
                self.db.rollback()
                return False  # A receipt is terminal; callbacks never move its deadline again.
            if row['state'] not in {'dispatch_intent','accepted','unknown'}:
                raise ValueError('Ricevuta incompatibile con un tentativo senza invio')
            binding = json.loads(row['binding_json'])
            if (receipt.get('app') != row['app'] or receipt.get('target_id') != binding['runtime_id']
                    or receipt.get('message_hash') != row['message_hash'] or not receipt.get('physical_id')
                    or receipt.get('received_at',0) < row['created']-1
                    or receipt.get('received_at',now+2) > now+1
                    or receipt.get('correlation') != token):
                raise ValueError('Ricevuta nativa non pertinente')
            job = json.loads(row['job_json'])
            self.db.execute('INSERT INTO native_receipts VALUES(?,?,?,?,?,?)',
                (row['app'],binding['runtime_id'],receipt['physical_id'],token,
                 receipt['received_at'],json.dumps(receipt)))
            self.db.execute("UPDATE native_attempts SET state='received',receipt_id=?,detail=? WHERE id=?",
                            (receipt['physical_id'],'Messaggio ricevuto nella chat',token))
            received = receipt['received_at']
            self.db.execute('''UPDATE timers SET phase=?,last_sent=?,due=?,detail=?
                WHERE id=? AND version=? AND phase='uncertain' ''',
                ('idle' if job.get('repeat',True) else 'finished',received,
                 received+job['interval_seconds'],'Messaggio ricevuto nella chat',row['job_id'],row['version']))
            self.db.execute('INSERT INTO message_events(at,job_id,title,event,detail) VALUES(?,?,?,?,?)',
                (received,row['job_id'],job['title'],'Consegnato con ricevuta','Ricevuta nativa '+receipt['physical_id']))
            self.db.execute('UPDATE message_failures SET resolved_at=? WHERE job_id=? AND version=? AND resolved_at IS NULL',
                (now,row['job_id'],row['version']))
            self.db.execute('DELETE FROM message_retry WHERE job_id=? AND version=?',(row['job_id'],row['version']))
            self._event(token,'received','Ricevuta nativa univoca verificata',now)
            self.db.commit()
            return True
        except BaseException:
            self.db.rollback()
            raise
