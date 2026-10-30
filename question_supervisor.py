"""Background coordinator for Sentinella's native question adapter."""
import json
from pathlib import Path
import subprocess
import time
from datetime import datetime
from collections import deque
from automation_policy import app_enabled, target_titles, title_allowed, action_block_reason, approval_actions_enabled
from polling_policy import ScanClock, interval
from message_engine import load_jobs
from bounded_process import run_bounded

from runtime_paths import data_root, command
ROOT = data_root()
LOGS = ROOT / "logs"


def connector_work_due(jobs, database, now=None, request_path=None):
    """Do not spawn a Python adapter every five seconds for a future timer."""
    import sqlite3
    from contextlib import closing
    now = time.time() if now is None else now
    selected = [j for j in jobs if j.get("enabled") and j.get("transport") == "codex_app" and j.get("thread_id")]
    if not selected:
        return False
    try:
        with closing(sqlite3.connect(Path(database).as_uri() + "?mode=ro", uri=True, timeout=.2)) as db:
            for job in selected:
                state = db.execute("SELECT version,phase,due FROM timers WHERE id=?", (job["id"],)).fetchone()
                if not state or state[0] != job["version"] or (state[1] == "idle" and state[2] <= now):
                    return True
                if db.execute("""SELECT 1 FROM connector_deliveries c JOIN local_codex_deliveries l ON l.token=c.token
                    WHERE c.job_id=? AND c.confirmed_at IS NULL AND l.stage IN ('reserved','drafted','sending','awaiting_receipt') LIMIT 1""", (job["id"],)).fetchone():
                    return True
            if request_path and Path(request_path).exists():
                request = json.loads(Path(request_path).read_text(encoding="utf-8"))
                if request.get("created_at", now+1) <= now <= request.get("expires_at", 0):
                    for job in selected:
                        if request.get("job_id") == job["id"] and request.get("version") == job["version"]:
                            if not db.execute("SELECT 1 FROM local_codex_verifications WHERE request_id=?", (request.get("request_id"),)).fetchone():
                                return True
        return False
    except (sqlite3.Error, OSError, ValueError):
        return True  # Let the bounded worker initialize or report the error.


def log(record):
    with (LOGS / "supervisor.jsonl").open("a", encoding="utf-8") as out:
        out.write(json.dumps({"time": datetime.now().astimezone().isoformat(), **record}, ensure_ascii=False) + "\n")


def run_adapter(app, title=None, act=False):
    arguments = ["--app", app]
    if title:
        arguments += ["--target-title", title]
    if act:
        arguments += ["--act"]
    return {"app": app, **bounded_worker("question_desktop.py", *arguments)}


def bounded_worker(script, *args):
    try:
        process = run_bounded(command("adapter_entry", script, *args), timeout=12, cwd=ROOT)
        if process["timed_out"]:
            log({"type": "adapter_timeout", "script": script, "elapsed_seconds": process["elapsed_seconds"], "stack": process["stderr"][-6000:]})
            return {"action": "error", "error": f"Timeout adattatore {script} dopo 12 secondi"}
        result = json.loads(process["stdout"].strip().splitlines()[-1])
        if process["returncode"] and not result.get("error"):
            result["error"] = f"Adattatore {script} terminato con codice {process['returncode']}"
        if result.get('error'):
            log({'type': 'adapter_error', 'script': script, 'error': result['error']})
        return result
    except (subprocess.TimeoutExpired, ValueError, IndexError, OSError) as exc:
        return {"action": "error", "error": f"{type(exc).__name__}: {exc}"}


def publish(state):
    from sentinel_launcher import atomic_json
    try:
        atomic_json(LOGS / "supervisor-health.json", state)
        return True
    except OSError as exc:
        # A diagnostic write must not stop the coordinator. Its next loop
        # publishes again; persistent failure remains visible to the watchdog.
        try:
            log({"type": "health_publish_error", "error": str(exc)})
        except OSError:
            pass
        return False


def process_target(config, app, title):
    """Follow a reported block; background sweeps are scheduled separately."""
    result = {"app": app, "action": "none"}
    if config.get("away_mode") and app_enabled(config, app, "questions"):
        result = run_adapter(app, title, act=True)
        if result.get("action", "none") != "none":
            return result
    if approval_actions_enabled(config, app) and title_allowed(config, app, title, "approvals"):
        # A notification can refer to an inline file card in another chat.
        selected = bounded_worker("chat_desktop.py", "--app", app, "--select-title", title)
        if selected.get("action") == "selected_chat":
            return selected
        if selected.get("current_title") == title:
            approval = bounded_worker("approval_desktop.py", "--app", app)
            if approval.get("invoked"):
                kind = approval.get("request_type", "file")
                log({"type": kind + "_approval", "target": title, **approval})
                return {**approval, "action": "approved_" + kind}
            error = approval.get("error", "").casefold()
            if any(message in error for message in ("pulsante coperto o ricreato", "la richiesta è cambiata", "clic rinviato")):
                # Activation and Electron layout can settle after the first
                # hit-test. Start over with fresh controls; never click stale ones.
                return {**approval, "action": "waiting_file_approval", "retryable": True}
            if any(dialog.get("eligible") or dialog.get("waiting_for_resource") for dialog in approval.get("dialogs", [])):
                return {**approval, "action": "waiting_file_approval"}
            if any(dialog.get("manual_required") for dialog in approval.get("dialogs", [])):
                return {**approval, "action": "manual_required"}
            return {**approval, "action": "none"}
        return selected
    return result


def scheduled_scan(config, app, scans, now, inspect_only=False):
    """At most one sweep per interval per app, regardless of loop frequency."""
    if not scans.due(app, now, interval(config, "chats")):
        return None
    if action_block_reason(config) and (not inspect_only or config.get("paused")):
        return None
    result = run_adapter(app, act=False)
    if result.get("windows") == 0 or result.get("available") is False or result.get("error") or "Desktop bloccato" in result.get("reason", ""):
        scans.defer(app, now, 30)
        log({"type": "waiting_for_app", "app": app, "retry_seconds": 30, "error": result.get("error")})
        return {"question": result, "approval": None, "ready": False}
    approval = None
    if approval_actions_enabled(config, app):
        approval = bounded_worker("approval_desktop.py", "--app", app, *(["--inspect-only"] if inspect_only else []))
        if approval.get("invoked") or approval.get("error"):
            log({"type": approval.get("request_type", "file") + "_approval", **approval})
        if approval.get("error") or approval.get("available") is False or approval.get("windows") == 0:
            scans.defer(app, now, 30)
            log({"type": "waiting_for_app", "app": app, "stage": "approvals", "retry_seconds": 30,
                 "error": approval.get("error"), "reason": approval.get("reason")})
            return {"question": result, "approval": approval, "ready": False}
    if approval and approval.get("invoked"):
        # A model can immediately request the parent directory or another file.
        # Follow that chain once; an empty scan restores the normal interval.
        scans.defer(app, now, 2)
    else:
        scans.mark(app, now)
    log({"type": "chat_scan", "app": app, "interval_seconds": interval(config, "chats"), "error": result.get("error")})
    return {"question": result, "approval": approval, "ready": True}


def pending_scan_titles(scan):
    result = scan["question"]
    titles = list(result.get("pending_chats", []))
    dialogs = (scan.get("approval") or {}).get("dialogs", [])
    pending_file = any(dialog.get("eligible") or dialog.get("waiting_for_resource") for dialog in dialogs)
    if result.get("question_buttons") or result.get("question") or pending_file:
        titles.append(result.get("current_title"))
    return list(dict.fromkeys(title for title in titles if title))


def run():
    LOGS.mkdir(exist_ok=True)
    # A restart must not turn days of saved notifications into fresh work.
    # Initial live scans discover still-pending cards; subsequent notifications
    # are read from this boundary. The history remains available for diagnosis.
    event_path = LOGS / "events.jsonl"
    offset = event_path.stat().st_size if event_path.exists() else 0
    jobs = deque()
    queued = set()
    cooldown = {}
    scans = ScanClock()
    replies = 0
    next_message_check = 0
    next_connector_check = 0
    next_recovery_check = 0
    last_checks = {}
    waiting_apps = set()
    last_scan_results = {}
    last_target_result = None
    last_command_option = None

    def enqueue(config, app, title):
        key = (app, title)
        # A title is a lookup key, never a command from notification contents.
        questions = config.get("away_mode") and app_enabled(config, app, "questions") and title_allowed(config, app, title, "questions")
        approvals = approval_actions_enabled(config, app) and title_allowed(config, app, title, "approvals")
        if title and key not in queued and (questions or approvals):
            jobs.append((app, title, 0))
            queued.add(key)

    def accept_scan(config, app, scan, state):
        if scan.get("ready"):
            waiting_apps.discard(app)
            last_checks[app] = datetime.now().astimezone().isoformat()
        else:
            waiting_apps.add(app)
        result = scan["question"]
        last_scan_results[app] = {"ready": scan["ready"], "question_windows": result.get("windows"),
                                  "question_error": result.get("error"),
                                  "approval_error": (scan.get("approval") or {}).get("error"),
                                  "partial": bool(result.get('partial') or (scan.get('approval') or {}).get('partial')),
                                  "partial_reason": result.get('partial_reason') or (scan.get('approval') or {}).get('partial_reason'),
                                  "manual_approvals": [d.get("reason") for d in (scan.get("approval") or {}).get("dialogs", []) if d.get("manual_required")],
                                  "reason": result.get("reason") or (scan.get("approval") or {}).get("reason")}
        state.update(last_result=result, waiting_for_apps=sorted(waiting_apps), last_scan_results=last_scan_results)
        state["next_chat_scan_in_seconds"][app] = round(scans.remaining(app, time.monotonic(), interval(config, "chats")))
        for title in pending_scan_titles(scan):
            enqueue(config, app, title)

    while True:
        config = json.loads((ROOT / "approval-config.json").read_text(encoding="utf-8"))
        command_option = bool(config.get("automatic_command_approval", False))
        if command_option != last_command_option:
            scans = ScanClock()
            last_command_option = command_option
        enabled_apps = [app for app in config["apps"] if app_enabled(config, app, "questions") or app_enabled(config, app, "approvals") or approval_actions_enabled(config, app)]
        active = config.get("away_mode", False)
        event_path = LOGS / "events.jsonl"
        if event_path.exists():
            with event_path.open("r", encoding="utf-8") as stream:
                if offset > event_path.stat().st_size:
                    offset = 0
                stream.seek(offset)
                while True:
                    before = stream.tell()
                    line = stream.readline()
                    if not line:
                        break
                    if not line.endswith("\n"):
                        stream.seek(before)
                        break
                    try:
                        record = json.loads(line)
                        source = record.get("app_id", "").casefold()
                        app = "codex" if source.startswith("openai.codex_") else "claude" if "claude" in source else None
                        body = record.get("body", "").casefold()
                        if record.get("type") == "notification" and app in enabled_apps and any(s in body for s in ("ha una domanda", "needs your input", "has a question", "requires your input", "autorizzazione", "permission", "approval", "consenti")):
                            enqueue(config, app, record["title"])
                    except (ValueError, KeyError):
                        pass
                offset = stream.tell()
        state = {"updated_at": datetime.now().astimezone().isoformat(), "active": active, "paused": config.get("paused", False), "queued": len(jobs), "replies_this_run": replies,
                 "last_target_result": last_target_result,
                 "question_scopes": {app: target_titles(config, app, "questions") for app in enabled_apps if app_enabled(config, app, "questions")},
                 "chat_scan_seconds": interval(config, "chats"), "last_chat_checks": last_checks,
                 "waiting_for_apps": sorted(waiting_apps),
                 "last_scan_results": last_scan_results,
                 "next_chat_scan_in_seconds": {app: round(scans.remaining(app, time.monotonic(), interval(config, "chats"))) for app in enabled_apps}}
        blocked = action_block_reason(config)
        # This worker belongs to Sentinella, not to a chat heartbeat. Receipts
        # can be read while the PC is in use; it gates all native actions itself.
        if not config.get("paused") and time.monotonic() >= next_connector_check:
            connector = bounded_worker("codex_local_sender.py") if connector_work_due(load_jobs(), LOGS / "messages.sqlite3", request_path=LOGS / "codex-local-verify-request.json") else {"action": "idle"}
            next_connector_check = time.monotonic() + (1 if connector.get("action") in {"selected_chat", "drafted"} else 5)
            state["codex_delivery"] = connector
            if connector.get("action") in {"selected_chat", "drafted", "awaiting_receipt", "confirmed"}:
                publish(state)
                time.sleep(1)
                continue
        if blocked:
            state.update(blocked_reason=blocked, messages={"action": "paused" if config.get("paused") else "waiting_for_idle"})
            # The sender verifies foreground ownership before every native
            # action. It can operate on the already-open background chat without
            # taking focus; a foreground target still honors the idle threshold.
            if not config.get('paused') and time.monotonic() >= next_message_check:
                if any(job.get('enabled') and job['app'] == 'claude' and job.get('transport', 'desktop') == 'desktop' for job in load_jobs()):
                    periodic = bounded_worker('message_worker.py', '--skip-app', 'codex')
                    state['messages'] = periodic
                next_message_check = time.monotonic() + 5
            # Human input blocks focus/clicks/sends, not a read-only observation.
            # Preserve the configured scan interval and never inspect on pause.
            if not config.get("paused"):
                for app in enabled_apps:
                    scan = scheduled_scan(config, app, scans, time.monotonic(), inspect_only=True)
                    if scan is not None:
                        accept_scan(config, app, scan, state)
                        state["scan_mode"] = "read_only"
                        break
            state["queued"] = len(jobs)
            publish(state)
            time.sleep(2)
            continue
        if config.get("automatic_file_recovery") and time.monotonic() >= next_recovery_check:
            recovery = bounded_worker("file_recovery.py")
            next_recovery_check = time.monotonic() + (1 if recovery.get("action") in {"selected_chat", "drafted"} else 10)
            state["file_recovery"] = recovery
            if recovery.get("action") not in {"idle", "paused", "waiting_for_idle"}:
                log({"type": "file_recovery", **recovery})
            if recovery.get("action") in {"selected_chat", "drafted"}:
                publish(state)
                time.sleep(1)
                continue
        ready_job = None
        for _ in range(len(jobs)):
            candidate = jobs.popleft()
            if cooldown.get(candidate[:2], 0) <= time.monotonic():
                ready_job = candidate
                break
            jobs.append(candidate)
        if ready_job:
            app, title, attempts = ready_job
            key = (app, title)
            result = process_target(config, app, title)
            last_target_result = {"target": title, "result": result, "updated_at": datetime.now().astimezone().isoformat()}
            state["last_target_result"] = last_target_result
            state.update(last_target=title, last_result=result)
            action = result.get("action", "none")
            if action != "none" or result.get("error") or result.get("reason"):
                log({"target": title, **result})
            if action in ("sent_reply", "approved_file", "approved_command"):
                if action == "sent_reply":
                    replies += 1
                    state["replies_this_run"] = replies
                queued.discard(key)
                cooldown[key] = time.monotonic() + 30
                if action in ("approved_file", "approved_command"):
                    scans.defer(app, time.monotonic(), 2)
            elif action in ("selected_chat", "opened_question", "drafted_reply", "waiting_file_approval") and attempts < 12:
                jobs.appendleft((app, title, attempts + 1))
            elif action == "manual_required":
                queued.discard(key)
                cooldown[key] = time.monotonic() + 300
            elif attempts < 3:
                cooldown[key] = time.monotonic() + 30
                jobs.append((app, title, attempts + 1))
            else:
                queued.discard(key)
                cooldown[key] = time.monotonic() + 300
        else:
            # Timer deadlines are independent of the background chat scan interval.
            periodic = {"action": "idle"}
            if time.monotonic() >= next_message_check:
                if any(job.get("enabled") and job.get("transport", "desktop") == "desktop" and job["app"] not in waiting_apps for job in load_jobs()):
                    skip_args = [argument for app in sorted(waiting_apps) for argument in ("--skip-app", app)]
                    periodic = bounded_worker("message_worker.py", *skip_args)
                next_message_check = time.monotonic() + (0.8 if periodic.get("action") in {"selected_chat", "drafted"} else 5)
                if periodic.get("action") not in {"idle", "none"}:
                    log({"type": "periodic_message", **periodic})
            state["messages"] = periodic
            if periodic.get("action") not in {"selected_chat", "drafted", "sent_periodic"}:
                for app in enabled_apps:
                    scan = scheduled_scan(config, app, scans, time.monotonic())
                    if scan is None:
                        continue
                    accept_scan(config, app, scan, state)
                    break  # Each app has its own deadline; scan the other next loop.
        state["queued"] = len(jobs)
        publish(state)
        time.sleep(0.8 if jobs else 2)


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        log({"state": "failed", "error": f"{type(exc).__name__}: {exc}"})
        raise
