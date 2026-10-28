"""Sentinella-owned native delivery with a durable reservation and real receipt.

No model/agent, heartbeat automation, private API, or writes to Codex storage.
Codex metadata/history is opened read-only; Send uses the existing native adapter.
"""
from datetime import datetime
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time

from automation_policy import action_block_reason, load_config, native_delivery_config
from codex_delivery import Delivery
from message_engine import ROOT, TimerStore


def audit(action, **fields):
    event = {"timestamp": datetime.now().astimezone().isoformat(), "source": "sentinella_local",
             "pid": os.getpid(), "parent_pid": os.getppid(), "action": action, **fields}
    with (ROOT / "logs/codex-delivery-events.jsonl").open("a", encoding="utf-8") as out:
        out.write(json.dumps(event, ensure_ascii=False) + "\n")


def thread_metadata(job, home=None):
    home = Path(home) if home else Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    paths = list(home.glob("state_[0-9]*.sqlite"))
    if not paths:
        raise ValueError("Indice locale Codex non disponibile")
    path = max(paths, key=lambda p: int(p.stem.split("_")[-1]))
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=.2)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT id, COALESCE(NULLIF(name,''),title) AS name, rollout_path, archived FROM threads WHERE id=?", (job["thread_id"],)).fetchone()
        if not row or row["archived"] or row["name"] != job["title"]:
            raise ValueError("ID e titolo della chat configurata non corrispondono")
        duplicates = connection.execute("SELECT count(*) FROM threads WHERE archived=0 AND COALESCE(NULLIF(name,''),title)=?", (job["title"],)).fetchone()[0]
        if duplicates != 1:
            raise ValueError("Titolo Codex duplicato: invio nativo sospeso")
        result = dict(row)
    history = Path(result["rollout_path"])
    with history.open("rb") as stream:
        header = json.loads(stream.readline(2 * 1024 * 1024))
    if header.get("type") != "session_meta" or header.get("payload", {}).get("id") != job["thread_id"]:
        raise ValueError("Identita dello storico Codex non verificata")
    return result


def find_user_receipt(path, thread_id, expected_hash, reserved_at, offset, now=None):
    """Read only newly appended, complete records; assistant text is never proof."""
    now = time.time() if now is None else now
    path = Path(path)
    with path.open("rb") as stream:
        header = json.loads(stream.readline(2 * 1024 * 1024))
        if header.get("type") != "session_meta" or header.get("payload", {}).get("id") != thread_id:
            raise ValueError("Storico di una chat diversa")
        if path.stat().st_size < offset:
            raise ValueError("Storico ricreato dopo la prenotazione: verifica necessaria")
        stream.seek(offset)
        data = stream.read(8 * 1024 * 1024)
    cursor = offset
    for line in data.splitlines(keepends=True):
        if not line.endswith(b"\n"):
            break
        position = cursor
        cursor += len(line)
        try:
            record = json.loads(line)
            payload = record.get("payload", {})
            stamp = datetime.fromisoformat(record.get("timestamp", "").replace("Z", "+00:00")).timestamp()
        except (ValueError, TypeError, AttributeError):
            continue
        if record.get("type") != "response_item" or payload.get("type") != "message" or payload.get("role") != "user":
            continue
        if stamp + 1 < reserved_at or stamp > now + 1:
            continue
        texts = [item.get("text", "") for item in payload.get("content", []) if item.get("type") in {"input_text", "text"}]
        for text in texts:
            digest = hashlib.sha256(text.encode()).hexdigest()
            # Codex's native composer serializes one terminal line break. Keep
            # the original record intact and permit only this observed wrapper,
            # never general whitespace stripping or substring matches.
            terminal_lf = text.endswith("\n") and hashlib.sha256(text[:-1].encode()).hexdigest() == expected_hash
            if digest == expected_hash or terminal_lf:
                return {"thread": {"id": thread_id}, "source": str(path), "byte_offset": position,
                        "configured_message_hash": expected_hash, "observed_message_hash": digest,
                        "native_terminal_lf": digest != expected_hash, "record": record}, cursor
    return None, cursor


def observed_target(config, job):
    from chat_desktop import snapshot, entries, timer_target
    controls, current = snapshot(config, "codex")
    matches = timer_target(entries(controls, "codex"), job)
    if len(matches) != 1 or matches[0][0]["ambiguous"]:
        raise ValueError("Chat nativa non univoca o non disponibile")
    row = matches[0][0]
    states = {part.strip().casefold() for part in row.get("status", "").split(",")}
    status = "active" if states & {"in elaborazione", "in esecuzione", "running", "working"} else "idle"
    return current, status


def verification_request(job, db, now):
    path = ROOT / "logs/codex-local-verify-request.json"
    if not path.exists():
        return None
    request = json.loads(path.read_text(encoding="utf-8"))
    if (request.get("job_id") != job["id"] or request.get("version") != job["version"]
            or request.get("action") != "one_authorized_delivery"
            or not request.get("request_id") or not request.get("created_at", now + 1) <= now <= request.get("expires_at", 0)):
        return None
    if db.execute("SELECT 1 FROM local_codex_verifications WHERE request_id=?", (request["request_id"],)).fetchone():
        return None
    return request


def tick(store=None, bridge=None, now=None, metadata=thread_metadata, observe=observed_target, adapter=None):
    from chat_desktop import step, select_chat
    real_clock = now is None
    now = time.time() if real_clock else now
    selected_job = {}
    delivery_config = lambda: native_delivery_config(allow_present=selected_job.get('send_when_present'))
    adapter = adapter or (lambda config, job, delivery: step(config, job, delivery, live_guard=lambda: action_block_reason(delivery_config())))
    owned = store is None
    store = store or TimerStore(ROOT / "logs/messages.sqlite3")
    bridge = bridge or Delivery(store, config_reader=delivery_config)
    db = store.db
    db.execute("""CREATE TABLE IF NOT EXISTS local_codex_deliveries
        (token TEXT PRIMARY KEY, stage TEXT, history_path TEXT, scan_offset INTEGER, status_at_claim TEXT)""")
    columns = {row[1] for row in db.execute("PRAGMA table_info(local_codex_deliveries)")}
    if "initial_offset" not in columns:
        db.execute("ALTER TABLE local_codex_deliveries ADD COLUMN initial_offset INTEGER")
    if "receipt_revision" not in columns:
        db.execute("ALTER TABLE local_codex_deliveries ADD COLUMN receipt_revision INTEGER DEFAULT 0")
    db.execute("CREATE TABLE IF NOT EXISTS local_codex_verifications (request_id TEXT PRIMARY KEY, token TEXT)")
    db.commit()
    try:
        config = bridge.config_reader()
        if config.get("paused") or not bridge.running():
            return {"action": "paused"}
        jobs = bridge.jobs()
        for job in jobs:
            selected_job = job
            config = bridge.config_reader()
            state = store.prepare(job, now)
            verification = verification_request(job, db, now)
            pending = db.execute("""SELECT c.*, l.stage, l.history_path, l.scan_offset, l.status_at_claim, l.initial_offset, l.receipt_revision
                FROM connector_deliveries c LEFT JOIN local_codex_deliveries l ON c.token=l.token
                WHERE c.job_id=? AND c.confirmed_at IS NULL ORDER BY c.reserved_at LIMIT 1""", (job["id"],)).fetchone()
            if pending:
                pending = dict(pending)
                if pending["stage"] in {"sending", "awaiting_receipt"}:
                    if (pending["receipt_revision"] or 0) < 1:
                        # Reconcile an existing reservation after a parser fix.
                        # Re-read authentic history, never resend or redate it.
                        beginning = pending["initial_offset"]
                        if beginning is None:
                            beginning = max(0, Path(pending["history_path"]).stat().st_size - 8 * 1024 * 1024)
                        pending["scan_offset"] = beginning
                        db.execute("UPDATE local_codex_deliveries SET scan_offset=?, initial_offset=?, receipt_revision=1 WHERE token=?", (beginning, beginning, pending["token"])); db.commit()
                    receipt, cursor = find_user_receipt(pending["history_path"], pending["thread_id"], pending["message_hash"], pending["reserved_at"], pending["scan_offset"], now)
                    if receipt:
                        receipt_path = ROOT / "logs" / f"codex-local-receipt-{pending['token']}.json"
                        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
                        result = bridge.confirm(pending["token"], f"local_history:{receipt['byte_offset']}:{receipt['record']['payload'].get('id')}", now)
                        db.execute("UPDATE local_codex_deliveries SET stage='confirmed' WHERE token=?", (pending["token"],)); db.commit()
                        audit("confirmed", token=pending["token"], job=job["id"], version=pending["version"], thread_id=pending["thread_id"], status_at_claim=pending["status_at_claim"], receipt_file=str(receipt_path))
                        return {**result, "token": pending["token"], "receipt_file": str(receipt_path)}
                    db.execute("UPDATE local_codex_deliveries SET scan_offset=? WHERE token=?", (cursor, pending["token"])); db.commit()
                    return {"action": "awaiting_receipt", "token": pending["token"], "job": job["id"]}
                if pending["stage"] not in {"reserved", "drafted"}:
                    return {"action": "uncertain", "job": job["id"], "reason": "Prenotazione preesistente: solo riconciliazione, nessun reinvio"}
                if pending["version"] != job["version"]:
                    return {"action": "changed_or_disabled", "job": job["id"], "reason": "Bozza di una versione precedente: nessun invio"}
            elif state["phase"] in {"uncertain", "finished"} or (state["due"] > now and not verification):
                continue
            blocked = action_block_reason(config)
            if blocked:
                return {"action": "waiting_for_idle", "job": job["id"], "reason": blocked}
            meta = metadata(job)
            current, status = observe(config, job)
            if status == "active" and not job.get("send_when_active"):
                return {"action": "waiting_for_chat", "job": job["id"]}
            if current != job["title"]:
                if config.get("native_background_delivery"):
                    return {"action": "waiting_for_idle", "job": job["id"], "reason": "Cambio chat rinviato: nessuna navigazione durante l'uso del PC"}
                result = select_chat(config, "codex", job["title"])
                return {**result, "job": job["id"]}
            if not pending:
                offset = Path(meta["rollout_path"]).stat().st_size
                claim = bridge.claim(job["id"], job["version"], status, now, allow_early=bool(verification))
                if claim.get("action") != "send":
                    return claim
                db.execute("INSERT INTO local_codex_deliveries (token,stage,history_path,scan_offset,status_at_claim,initial_offset,receipt_revision) VALUES (?, 'reserved', ?, ?, ?, ?, 1)", (claim["token"], meta["rollout_path"], offset, status, offset)); db.commit()
                if verification:
                    db.execute("INSERT INTO local_codex_verifications VALUES (?,?)", (verification["request_id"], claim["token"])); db.commit()
                pending = {"token": claim["token"], "stage": "reserved"}
                audit("reserved", token=claim["token"], job=job["id"], thread_id=job["thread_id"], status_at_claim=status,
                      verification_request=verification["request_id"] if verification else None, scheduled_due_before=state["due"])
            ready = bridge.ready(pending["token"])
            if ready.get("action") != "send":
                return ready
            result = adapter(config, job, {"drafted": pending["stage"] == "drafted"})
            if result.get("action") == "drafted":
                db.execute("UPDATE local_codex_deliveries SET stage='drafted' WHERE token=?", (pending["token"],)); db.commit()
                audit("drafted", token=pending["token"], job=job["id"])
                store.update(job, "uncertain", "Bozza di Sentinella preparata; consegna al prossimo controllo")
            elif result.get("action") == "ready":
                button = result.pop("button")
                if bridge.ready(pending["token"]).get("action") != "send" or metadata(job)["id"] != job["thread_id"]:
                    return {"action": "paused_or_changed"}
                current, status = observe(config, job)
                if current != job["title"] or (status == "active" and not job.get("send_when_active")):
                    return {"action": "chat_changed_before_send", "job": job["id"]}
                changed = db.execute("UPDATE local_codex_deliveries SET stage='sending' WHERE token=? AND stage='drafted'", (pending["token"],)).rowcount; db.commit()
                if changed != 1:
                    return {"action": "already_sending"}
                audit("sending", token=pending["token"], job=job["id"], thread_id=job["thread_id"], status_at_send=status)
                button.GetInvokePattern().Invoke()
                db.execute("UPDATE local_codex_deliveries SET stage='awaiting_receipt' WHERE token=?", (pending["token"],)); db.commit()
                return {"action": "awaiting_receipt", "job": job["id"], "token": pending["token"]}
            if result.get("reason"):
                store.update(job, "uncertain", result["reason"])
            return {**result, "job": job["id"], "token": pending["token"]}
        return {"action": "idle"}
    finally:
        if owned:
            store.db.close()


if __name__ == "__main__":
    try:
        result = tick()
        try:
            from sentinel_launcher import atomic_json
            atomic_json(ROOT / "logs/codex-local-status.json", {"updated_at": time.time(), **result})
        except OSError:
            pass  # A diagnostic file must not turn an accepted send into a retry.
        print(json.dumps(result, ensure_ascii=True), flush=True)
    except Exception as exc:
        audit("error", error=f"{type(exc).__name__}: {exc}")
        print(json.dumps({"action": "error", "error": f"{type(exc).__name__}: {exc}"}), flush=True)
        raise SystemExit(2)
