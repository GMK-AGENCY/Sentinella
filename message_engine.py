"""Durable, non-bursting timer state. One delivery is reserved before Send."""
import json
from pathlib import Path
import sqlite3
import time

from runtime_paths import data_root
ROOT = data_root()
CONFIG = ROOT / "messages-config.json"
from message_templates import DEFAULT_MESSAGE


def load_jobs():
    if not CONFIG.exists():
        return []
    return json.loads(CONFIG.read_text(encoding="utf-8"))["jobs"]


def save_jobs(jobs):
    temporary = CONFIG.with_suffix(".tmp")
    temporary.write_text(json.dumps({"jobs": jobs}, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(CONFIG)


def validate_job(job):
    if job.get("app") not in {"codex", "claude"} or not job.get("title") or not job.get("group"):
        raise ValueError("Seleziona una chat dall'elenco aggiornato")
    if not isinstance(job.get("message"), str) or not job["message"].strip() or len(job["message"]) > 12000:
        raise ValueError("Inserisci un messaggio da 1 a 12000 caratteri")
    seconds = float(job["interval_seconds"])
    if not 60 <= seconds <= 7 * 86400:
        raise ValueError("Intervallo consentito: da 1 minuto a 7 giorni")
    if not job.get("id") or not job.get("version"):
        raise ValueError("Identificativo della programmazione mancante")


class TimerStore:
    def __init__(self, path):
        Path(path).parent.mkdir(exist_ok=True)
        self.db = sqlite3.connect(path, timeout=1)
        self.db.row_factory = sqlite3.Row
        self.db.execute("CREATE TABLE IF NOT EXISTS timers (id TEXT PRIMARY KEY, version TEXT, due REAL, phase TEXT, last_sent REAL, detail TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS message_events (seq INTEGER PRIMARY KEY, at REAL, job_id TEXT, title TEXT, event TEXT, detail TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS message_retry (job_id TEXT PRIMARY KEY, version TEXT, retry_at REAL)")
        self.db.commit()

    def prepare(self, job, now):
        validate_job(job)
        row = self.db.execute("SELECT * FROM timers WHERE id=?", (job["id"],)).fetchone()
        if row is None or row["version"] != job["version"]:
            self.db.execute("INSERT OR REPLACE INTO timers VALUES (?,?,?,'idle',NULL,'In attesa del primo invio')", (job["id"], job["version"], job.get("activated_at", now) + job["interval_seconds"]))
            self.db.commit()
            self.event(job, "Programmato", f"Prima scadenza dopo {job['interval_seconds'] / 60:g} minuti")
            row = self.db.execute("SELECT * FROM timers WHERE id=?", (job["id"],)).fetchone()
        return dict(row)

    def update(self, job, phase, detail, due=None, sent=None):
        previous = self.db.execute("SELECT phase,detail,last_sent FROM timers WHERE id=? AND version=?", (job["id"], job["version"])).fetchone()
        if due is None:
            self.db.execute("UPDATE timers SET phase=?,detail=? WHERE id=? AND version=?", (phase, detail, job["id"], job["version"]))
        else:
            self.db.execute("UPDATE timers SET phase=?,detail=?,due=?,last_sent=COALESCE(?,last_sent) WHERE id=? AND version=?", (phase, detail, due, sent, job["id"], job["version"]))
        if previous and (previous["phase"] != phase or previous["detail"] != detail or sent is not None and previous["last_sent"] != sent):
            self.event(job, "Invio effettuato · ricezione non verificata" if sent is not None else "Da verificare" if phase == "uncertain" else "Preparazione" if phase == "drafted" else "In attesa", detail, at=sent)
        self.db.commit()

    def event(self, job, event, detail, at=None):
        self.db.execute("INSERT INTO message_events(at,job_id,title,event,detail) VALUES (?,?,?,?,?)", (time.time() if at is None else at, job["id"], job["title"], event, detail))
        self.db.commit()

    def history(self, job_id=None, since=None, outcome=None):
        clauses, args = [], []
        if job_id:
            clauses.append('job_id=?'); args.append(job_id)
        if since is not None:
            clauses.append('at>=?'); args.append(since)
        if outcome == 'confirmed':
            clauses.append("event='Consegnato con ricevuta'")
        elif outcome == 'attention':
            clauses.append("lower(event) LIKE '%verific%' AND event!='Consegnato con ricevuta'")
        elif outcome == 'waiting':
            clauses.append("event IN ('Programmato','In attesa','Preparazione','In pausa','Ripreso')")
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        rows = [dict(row) for row in self.db.execute('SELECT * FROM message_events' + where + ' ORDER BY at DESC,seq DESC LIMIT 200', args)]
        # Existing receipts remain useful after installing the new journal.
        # Import only actual persisted confirmations, never assistant prose.
        titles = {job['id']: job['title'] for job in load_jobs()}
        exists = self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='connector_deliveries'").fetchone()
        if exists and outcome not in {'attention', 'waiting'}:
            legacy_args = []
            legacy_where = 'confirmed_at IS NOT NULL AND receipt IS NOT NULL'
            if job_id:
                legacy_where += ' AND job_id=?'; legacy_args.append(job_id)
            if since is not None:
                legacy_where += ' AND confirmed_at>=?'; legacy_args.append(since)
            for item in self.db.execute('SELECT rowid AS receipt_id,job_id,confirmed_at FROM connector_deliveries WHERE ' + legacy_where + ' ORDER BY confirmed_at DESC LIMIT 200', legacy_args):
                if item['job_id'] not in titles or job_id and job_id != item['job_id']:
                    continue
                if not any(row['job_id'] == item['job_id'] and row['at'] == item['confirmed_at'] for row in rows):
                    rows.append({'legacy_id': item['receipt_id'], 'at': item['confirmed_at'], 'job_id': item['job_id'], 'title': titles[item['job_id']], 'event': 'Consegnato con ricevuta', 'detail': 'Ricevuta conservata nello storico precedente al nuovo registro'})
        return sorted(rows, key=lambda row: row['at'], reverse=True)[:200]

    def defer(self, job, now):
        self.db.execute("INSERT OR REPLACE INTO message_retry VALUES (?,?,?)", (job['id'], job['version'], now + 30))
        self.db.commit()

    def retry_pending(self, job, now):
        row = self.db.execute("SELECT retry_at FROM message_retry WHERE job_id=? AND version=?", (job['id'], job['version'])).fetchone()
        return row is not None and row[0] > now

    def rows(self):
        rows = [dict(r) for r in self.db.execute("SELECT * FROM timers")]
        exists = self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='local_codex_deliveries'").fetchone()
        if exists:
            pending = self.db.execute("""SELECT c.job_id,c.version,c.reserved_at,l.stage
                FROM connector_deliveries c JOIN local_codex_deliveries l ON l.token=c.token
                WHERE c.confirmed_at IS NULL ORDER BY c.reserved_at""")
            for item in pending:
                for row in rows:
                    if row["id"] == item["job_id"] and row["version"] == item["version"]:
                        row.update(local_stage=item["stage"], reserved_at=item["reserved_at"])
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='claude_timer_deliveries'").fetchone():
            for item in self.db.execute("SELECT job_id,version,stage,send_started,prepared_at FROM claude_timer_deliveries ORDER BY prepared_at"):
                for row in rows:
                    if row['id'] == item['job_id'] and row['version'] == item['version']:
                        stage = 'awaiting_receipt' if item['stage'] == 'sending' else 'drafted' if item['stage'] == 'prepared' else item['stage']
                        row.update(local_stage=stage, reserved_at=item['send_started'] or item['prepared_at'])
        return rows


def tick(config, store, now=None, adapter=None, reconcile_only=False):
    from chat_desktop import step
    from automation_policy import action_block_reason, live_block_reason
    if config.get("paused", False):
        return {"action": "paused", "reason": "Sentinella in pausa globale"}
    live_adapter = adapter is None
    real_clock = now is None
    now = time.time() if real_clock else now
    adapter = adapter or step
    jobs = load_jobs()
    for job in jobs:
        if not job.get("enabled"):
            continue
        if reconcile_only and job['app'] != 'claude':
            continue
        if job.get("transport", "desktop") != "desktop":
            continue  # An app connector owns this timer; never duplicate via UI.
        if job["app"] in config.get("temporarily_unavailable_apps", []):
            continue
        state = store.prepare(job, now)
        claude = None
        native_config = config
        guard = live_block_reason
        if live_adapter and job['app'] == 'claude':
            from claude_delivery import Delivery
            from automation_policy import native_delivery_config
            native_config = native_delivery_config(lambda: config, app='claude', allow_present=job.get('send_when_present'))
            guard = lambda: action_block_reason(native_delivery_config(app='claude', allow_present=job.get('send_when_present')))
            claude = Delivery(store, ROOT)
            reconciled = claude.reconcile(job, state)
            if reconciled and reconciled['action'] in {'awaiting_receipt', 'modified_submission'}:
                return {**reconciled, 'job': job['id']}
            if reconcile_only:
                if reconciled:
                    return {**reconciled, 'job': job['id']}
                continue
            if claude.cleanup_needed(job):
                blocked = action_block_reason(native_config)
                if blocked:
                    return {'action': 'receipt_confirmed' if reconciled else 'none', 'job': job['id'], 'reason': blocked}
                cleanup = adapter(native_config, job, {'cleanup': True, 'archive': lambda value: claude.archive(job, value)}, live_guard=guard)
                claude.cleanup_done(job, cleanup['action'])
                return {**cleanup, 'job': job['id']}
            # Receipt confirmation can change the deadline and phase.
            state = store.prepare(job, now)
        elif reconcile_only:
            continue
        if state["phase"] in {"uncertain", "finished"} or state["due"] > now or store.retry_pending(job, now):
            continue
        # A selected, enabled timer is its own delegation. The app's enabled
        # switch controls absence replies and file approvals, independently.
        blocked = action_block_reason(native_config) if live_adapter else None
        if blocked:
            store.update(job, state["phase"], blocked)
            store.defer(job, now)
            return {"action": "none", "job": job["id"], "reason": blocked}
        delivery = {"drafted": state["phase"] == "drafted"}
        if claude:
            delivery['own_draft'] = lambda: claude.prepare(job)
            reservation = claude.current(job)
            delivery['can_redraft'] = bool(reservation and reservation['stage'] == 'prepared' and reservation['send_started'] is None)
        result = adapter(native_config, job, delivery, live_guard=guard) if claude else adapter(config, job, delivery)
        action = result.get("action")
        if action == "drafted":
            store.update(job, "drafted", "Messaggio preparato: invio al prossimo controllo")
        elif action == 'draft_preserved':
            if claude:
                claude.cancel_before_send(job)
            store.update(job, 'idle', result.get('reason', 'Bozza modificata: conservata'))
            store.defer(job, now)
        elif action == "ready":
            button = result.pop("button")
            validate_draft = result.pop('validate_draft', lambda: True)
            blocked = guard() if live_adapter else None
            if blocked:
                store.update(job, state["phase"], blocked)
                store.defer(job, now)
                return {"action": "none", "job": job["id"], "reason": blocked}
            # A pause/edit between preparation and Send takes effect immediately.
            current = next((j for j in load_jobs() if j["id"] == job["id"]), None)
            if current is None or not current.get("enabled") or any(current.get(k) != job.get(k) for k in ['version', 'message', 'app', 'title', 'group', 'send_when_active', 'send_when_present']) or current.get("transport", "desktop") != "desktop":
                return {"action": "cancelled", "job": job["id"]}
            if not validate_draft():
                if claude:
                    claude.cancel_before_send(job)
                store.update(job, 'idle', 'Bozza modificata prima dell’invio: conservata nel campo')
                store.defer(job, now)
                return {'action': 'draft_preserved', 'job': job['id']}
            store.update(job, "uncertain", "Invio iniziato: verifica necessaria se il processo si interrompe")
            if claude:
                claude.begin_send(job)
                if guard() or not validate_draft():
                    claude.cancel_before_send(job)
                    store.update(job, 'idle', 'Invio annullato prima del pulsante: bozza conservata')
                    store.defer(job, now)
                    return {'action': 'draft_preserved', 'job': job['id']}
            button.GetInvokePattern().Invoke()
            if claude:
                return {'action': 'awaiting_receipt', 'job': job['id'], 'reason': 'Invio avviato; attendo il messaggio utente nella cronologia Claude'}
            sent = time.time() if real_clock else now
            store.update(job, "idle" if job.get("repeat", True) else "finished", "Messaggio inviato", due=sent + job["interval_seconds"], sent=sent)
            result["action"] = "sent_periodic"
        elif action == "uncertain":
            store.update(job, "uncertain", result["reason"])
        elif action == "selected_chat":
            # Keep ownership across navigation/draft/send; a backoff here lets
            # a question scan switch chats between the steps indefinitely.
            store.update(job, state["phase"], "Chat selezionata")
        else:
            store.update(job, state["phase"], result.get("reason", "Apertura della chat"))
            store.defer(job, now)
        return {**result, "job": job["id"]}
    return {"action": "idle"}
