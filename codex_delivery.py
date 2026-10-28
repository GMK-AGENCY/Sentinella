"""Durable handoff for Codex's supported chat connector, without UI clicks.

The Codex heartbeat performs read_thread/send_message_to_thread; this module
owns the timer reservation and receipt. It never invokes private app APIs.
"""
import argparse
import hashlib
import json
import time
import uuid
from pathlib import Path

from automation_policy import load_config, action_block_reason
from message_engine import ROOT, TimerStore, load_jobs


def session_running(root=ROOT, now=None):
    now = time.time() if now is None else now
    try:
        import psutil
        heartbeat = json.loads((root / "logs/gui-heartbeat.json").read_text(encoding="utf-8"))
        proc = psutil.Process(heartbeat["pid"])
        return (0 <= now - heartbeat["updated_at"] < 45
                and Path(proc.cwd()).resolve() == root.resolve()
                and any("notification_watch.py" in arg for arg in proc.cmdline()))
    except (OSError, ValueError, KeyError, psutil.Error):
        return False


class Delivery:
    def __init__(self, store, config_reader=load_config, jobs_reader=load_jobs, running=session_running):
        self.store = store
        self.config_reader = config_reader
        self.jobs_reader = jobs_reader
        self.running = running
        store.db.execute("""CREATE TABLE IF NOT EXISTS connector_deliveries
            (token TEXT PRIMARY KEY, job_id TEXT, version TEXT, thread_id TEXT,
             message_hash TEXT, reserved_at REAL, confirmed_at REAL, receipt TEXT)""")
        store.db.commit()

    def jobs(self):
        return [job for job in self.jobs_reader() if job.get("app") == "codex"
                and job.get("transport") == "codex_app"
                and job.get("thread_id") and job.get("enabled")]

    def plan(self, now=None):
        now = time.time() if now is None else now
        config = self.config_reader()
        if config.get("paused") or not self.running():
            return {"action": "paused", "jobs": []}
        blocked = action_block_reason(config)
        result = []
        pending = []
        for job in self.jobs():
            state = self.store.prepare(job, now)
            if state["phase"] == "uncertain":
                pending.extend(dict(row) for row in self.store.db.execute("SELECT token,job_id,thread_id,message_hash,reserved_at FROM connector_deliveries WHERE job_id=? AND version=? AND confirmed_at IS NULL", (job["id"], job["version"])))
            if state["phase"] in {"uncertain", "finished"} or state["due"] > now:
                continue
            if blocked:
                self.store.update(job, state["phase"], blocked)
                continue
            result.append({**{k: job[k] for k in ("id", "version", "thread_id", "title", "message")},
                           "send_when_active": job.get("send_when_active") is True})
        return {"action": "due" if result else "waiting", "jobs": result, "pending_confirmation": pending}

    def ready(self, token):
        row = self.store.db.execute("SELECT * FROM connector_deliveries WHERE token=?", (token,)).fetchone()
        if row is None or row["confirmed_at"] is not None:
            return {"action": "not_ready"}
        if action_block_reason(self.config_reader()) or not self.running():
            return {"action": "paused"}
        job = next((j for j in self.jobs() if j["id"] == row["job_id"] and j["version"] == row["version"]), None)
        if not job or job["thread_id"] != row["thread_id"] or hashlib.sha256(job["message"].encode()).hexdigest() != row["message_hash"]:
            return {"action": "changed_or_disabled"}
        return {"action": "send", "token": token, "thread_id": job["thread_id"], "message": job["message"]}

    def claim(self, job_id, version, status, now=None, allow_early=False):
        now = time.time() if now is None else now
        if allow_early:
            # Only the local sender's explicit, one-use verification request
            # uses this path. The scheduled CLI claim has no early-send flag.
            if action_block_reason(self.config_reader()) or not self.running():
                return {"action": "not_due_or_paused"}
            plan = {"jobs": self.jobs()}
        else:
            plan = self.plan(now)
        job = next((j for j in plan["jobs"] if j["id"] == job_id and j["version"] == version), None)
        if job is None:
            return {"action": "not_due_or_paused"}
        if status not in {"idle", "notLoaded"} and not (status == "active" and job.get("send_when_active") is True):
            self.store.db.execute("UPDATE timers SET detail=? WHERE id=? AND version=?", ("Chat in elaborazione: collegamento Codex in attesa", job_id, version))
            self.store.db.commit()
            return {"action": "waiting_for_chat"}
        token = str(uuid.uuid4())
        db = self.store.db
        try:
            db.execute("BEGIN IMMEDIATE")
            deadline = "" if allow_early else " AND due<=?"
            args = ("Invio tramite collegamento Codex prenotato: non ripetere senza ricevuta", job_id, version)
            changed = db.execute("UPDATE timers SET phase='uncertain',detail=? WHERE id=? AND version=? AND phase='idle'" + deadline, args if allow_early else (*args, now)).rowcount
            if changed != 1:
                db.rollback()
                return {"action": "already_reserved"}
            digest = hashlib.sha256(job["message"].encode()).hexdigest()
            db.execute("INSERT INTO connector_deliveries VALUES (?,?,?,?,?,?,NULL,NULL)", (token, job_id, version, job["thread_id"], digest, now))
            db.commit()
        except Exception:
            db.rollback()
            raise
        return {"action": "send", "token": token, **job}

    def confirm(self, token, receipt, now=None):
        now = time.time() if now is None else now
        record = self.store.db.execute("SELECT * FROM connector_deliveries WHERE token=?", (token,)).fetchone()
        if record is None or record["confirmed_at"] is not None:
            return {"action": "unknown_or_already_confirmed"}
        if not receipt or len(receipt) > 2000:
            raise ValueError("Ricevuta di messaggio letto in chat obbligatoria")
        job = next((j for j in self.jobs_reader() if j["id"] == record["job_id"]), None)
        self.store.db.execute("UPDATE connector_deliveries SET confirmed_at=?,receipt=? WHERE token=?", (now, receipt, token))
        if job and job["version"] == record["version"]:
            self.store.db.execute("UPDATE timers SET phase=?,detail=?,due=?,last_sent=? WHERE id=? AND version=? AND phase='uncertain'", ("idle" if job.get("repeat", True) else "finished", "Messaggio ricevuto in chat tramite collegamento Codex", now + job["interval_seconds"], now, job["id"], job["version"]))
        self.store.db.commit()
        if job:
            self.store.event(job, "Consegnato con ricevuta", "Messaggio utente autentico verificato nella chat", at=now)
        return {"action": "confirmed", "job": record["job_id"], "last_sent": now}

    def confirm_readback(self, token, readback, now=None):
        now = time.time() if now is None else now
        record = self.store.db.execute("SELECT * FROM connector_deliveries WHERE token=?", (token,)).fetchone()
        if record is None or readback.get("thread", {}).get("id") != record["thread_id"]:
            return {"action": "receipt_mismatch"}
        for turn in readback.get("turns", []):
            for item in turn.get("items", []):
                observed_at = float(turn.get("startedAt") or 0)
                # Active chat deliveries can join an already running turn.
                # Official item UUIDv7 IDs encode Unix milliseconds (RFC9562
                # section 5.7); never substitute the later readback time.
                identifier = item.get("id") or ""
                if identifier.startswith("fco_"):
                    identifier = identifier[4:]
                try:
                    item_uuid = uuid.UUID(identifier)
                    if item_uuid.version == 7 and item_uuid.variant == uuid.RFC_4122:
                        observed_at = (item_uuid.int >> 80) / 1000
                except (ValueError, AttributeError, TypeError):
                    pass  # Unknown ID formats require a sufficiently recent turn.
                if observed_at + 1 < record["reserved_at"] or observed_at > now + 1:
                    continue
                texts = []
                if item.get("type") == "userMessage":
                    texts = [c.get("text", "") for c in item.get("content", []) if c.get("type") == "text"]
                elif item.get("type") == "functionCallOutput" and item.get("name") == "send_message_to_thread" and item.get("namespace") == "codex_app":
                    output = item.get("output", {})
                    text = output.get("text", "") if isinstance(output, dict) else output
                    if isinstance(output, dict) and output.get("truncated"):
                        continue
                    if text.startswith("<codex_delegation>") and "<input>" in text and "</input>" in text:
                        texts = [text.split("<input>", 1)[1].rsplit("</input>", 1)[0]]
                if any(hashlib.sha256(text.encode()).hexdigest() == record["message_hash"] for text in texts):
                    receipt = f"read_thread:{turn['id']}:{item['id']}"
                    return self.confirm(token, receipt, now)
        return {"action": "receipt_not_found"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("plan", "claim", "ready", "confirm"))
    parser.add_argument("--job")
    parser.add_argument("--version")
    parser.add_argument("--status")
    parser.add_argument("--token")
    parser.add_argument("--readback")
    args = parser.parse_args()
    store = TimerStore(ROOT / "logs/messages.sqlite3")
    try:
        bridge = Delivery(store)
        if args.action == "plan":
            result = bridge.plan()
        elif args.action == "claim":
            result = bridge.claim(args.job, args.version, args.status)
        elif args.action == "ready":
            result = bridge.ready(args.token)
        else:
            if not args.readback:
                raise ValueError("Serve il risultato reale di read_thread, non una ricevuta dichiarata")
            result = bridge.confirm_readback(args.token, json.loads(Path(args.readback).read_text(encoding="utf-8")))
        print(json.dumps(result, ensure_ascii=True))
    finally:
        store.db.close()


if __name__ == "__main__":
    main()
