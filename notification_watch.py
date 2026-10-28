"""Windows notifications and the user's fixed absence reply for question cards."""
import json
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
import tkinter as tk
from datetime import datetime

from notification_core import Notification, Deduplicator, classify, after_cleanup
from service_recovery import ServiceRecovery, supervisor_stale

from runtime_paths import data_root, command
ROOT = data_root()
LOGS = ROOT / "logs"


class Monitor:
    def __init__(self):
        LOGS.mkdir(exist_ok=True)
        self.queue = queue.Queue()
        self.dedup = Deduplicator()
        self.rows = {}
        try:self.notifications_cleared_at=float(json.loads((LOGS/'notifications-cleared.json').read_text(encoding='utf-8'))['cleared_at'])
        except (OSError,ValueError,TypeError,KeyError):self.notifications_cleared_at=0
        self.worker = None
        self.monitoring = True
        self.restarts = 0
        self.reader_recovery = ServiceRecovery()
        self.last_scan = None
        self.current_state = "starting"
        self.last_message = None
        self.last_health_read = 0
        self.last_supervisor_health = {}
        self.dashboard_signature = None
        self.supervisor = None
        self.supervisor_restarts = 0
        self.supervisor_recovery = ServiceRecovery()
        self.supervisor_started_wall = 0
        self.gui_profiler = None
        self.last_poll_success = time.time()
        self.run_id = os.environ.get("SENTINELLA_RUN_ID", str(uuid.uuid4()))
        from loop_diagnostics import LoopDiagnostics
        self.diagnostics = LoopDiagnostics(LOGS / f"gui-stall-{self.run_id}.log")
        self.diagnostics.heartbeat()
        from app_icon import set_process_identity
        self.taskbar_identity_set = set_process_identity()
        self.root = tk.Tk()
        self.root.report_callback_exception = self.diagnostics.callback_error
        if "--background" in sys.argv:
            self.root.withdraw()
        from workspace_ui import build_workspace
        build_workspace(self)
        self.refresh_dashboard()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.start_worker()
        self.start_supervisor()
        (LOGS / "monitor.pid").write_text(str(os.getpid()), encoding="ascii")
        self.root.after(500, self.poll)
        self.gui_heartbeat()
        if "--background" in sys.argv:
            self.root.deiconify()
            self.root.iconify()

    def log(self, record):
        with (LOGS / "events.jsonl").open("a", encoding="utf-8") as out:
            out.write(json.dumps({"observed_at": datetime.now().astimezone().isoformat(), **record}, ensure_ascii=False) + "\n")

    def gui_heartbeat(self):
        from sentinel_launcher import atomic_json
        try:
            atomic_json(LOGS / "gui-heartbeat.json", {"run_id": self.run_id, "pid": os.getpid(), "updated_at": time.time(), "poll_updated_at": getattr(self, "last_poll_success", time.time()), "taskbar_identity_set":getattr(self,'taskbar_identity_set',False)})
        except OSError as exc:
            try:
                self.log({"type": "gui_heartbeat_write_error", "error": str(exc)})
            except OSError:
                pass
        finally:
            if getattr(self, "diagnostics", None):
                self.diagnostics.heartbeat()
            self.root.after(5000, self.gui_heartbeat)

    def refresh_dashboard(self, config=None, health=None):
        from automation_policy import load_config
        from polling_policy import interval
        if health is not None:
            self.last_supervisor_health = health
        health = self.last_supervisor_health
        config = config or load_config()
        from workspace_ui import update_insights
        update_insights(self, config)
        paused = config.get("paused", False)
        from workspace_ui import apply_runtime_status
        apply_runtime_status(self, paused)
        recovery = {}
        if config.get("automatic_file_recovery"):
            try:
                recovery = json.loads((ROOT / "logs/file-recovery-status.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        recovery_problem = recovery.get("action") in {"error", "uncertain", "scope_changed", "identity_changed"}
        clock_text = datetime.now().astimezone().strftime("Ora del PC · %H:%M")
        if hasattr(self, "pc_clock") and self.pc_clock.get() != clock_text:
            self.pc_clock.set(clock_text)
        signature = (paused, (health or {}).get("blocked_reason"), tuple((health or {}).get("waiting_for_apps", [])), interval(config, "notifications"), interval(config, "chats"), recovery_problem, recovery.get("action"), recovery.get("target"))
        if signature == self.dashboard_signature:
            return
        self.dashboard_signature = signature
        waiting = (health or {}).get("waiting_for_apps", [])
        if paused:
            detail = "La lettura delle notifiche continua. Nessun cambio chat, consenso o invio."
        else:
            detail = ((recovery.get("error") or ("Recupero sospeso: " + recovery.get("target", "") + ". Nessun reinvio automatico.")) if recovery_problem else
                      (health or {}).get("blocked_reason") or ("Controlli nell'interfaccia in attesa: " + ", ".join(app.title() for app in waiting) if waiting else "Domande e consensi nelle cartelle delegate"))
        self.away_status.set(detail)
        self.schedule_status.set(f"Notifiche ogni {interval(config, 'notifications'):g} s    ·    Chat ogni {interval(config, 'chats') / 60:g} min    ·    Frequenze modificabili in Impostazioni")

    def restart_reader(self):
        self.stop_worker()
        self.monitoring = True
        self.restarts = 0
        self.reader_recovery = ServiceRecovery()
        self.start_worker()
        self.set_status("starting", "Riavvio del lettore notifiche…")

    def toggle_automations(self):
        path = ROOT / "approval-config.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        config["paused"] = not config.get("paused", False)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
        self.refresh_dashboard(config)
        if config["paused"] and self.supervisor:
            import psutil
            try:
                for child in psutil.Process(self.supervisor.pid).children(recursive=True):
                    try:
                        child.terminate()
                    except psutil.Error:
                        pass
            except psutil.Error:
                pass
        self.log({"type": "global_pause", "paused": config["paused"]})

    def start_supervisor(self):
        self.supervisor_started_wall = time.time()
        self.supervisor_recovery.started()
        try:
            self.supervisor = subprocess.Popen(command("question_supervisor"), cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
            self.dashboard_signature = None
            self.refresh_dashboard()
        except OSError as exc:
            self.supervisor = None
            self.recover_supervisor(f"Avvio non riuscito: {exc}")

    def recover_supervisor(self, reason):
        from sentinel_launcher import stop_tree
        if self.supervisor:
            stop_tree(self.supervisor)
            self.supervisor = None
        delay = self.supervisor_recovery.failed(time.monotonic())
        self.away_status.set(f"Controllore in ripristino: nuovo tentativo tra {delay} secondi.")
        self.log({"type": "supervisor_recovery", "reason": reason, "retry_seconds": delay})

    def check_supervisor(self, health):
        now = time.monotonic()
        if self.supervisor:
            if self.supervisor.poll() is not None:
                self.recover_supervisor("Processo interrotto")
            elif supervisor_stale(health, self.supervisor_started_wall, time.time()):
                self.recover_supervisor("Nessun aggiornamento per 90 secondi")
            elif health:
                self.supervisor_recovery.healthy(now)
        elif self.supervisor_recovery.due(now):
            self.supervisor_restarts += 1
            self.start_supervisor()
            self.log({"type": "supervisor_restart", "attempt": self.supervisor_restarts})

    def set_status(self, state, message):
        self.current_state = state
        self.reader_status.set(message)
        self.refresh_dashboard()
        self.log({"type": "status", "state": state, "message": message})
        self.write_health()

    def write_health(self):
        from sentinel_launcher import atomic_json
        data = {
            "updated_at": datetime.now().astimezone().isoformat(), "state": self.current_state,
            "last_successful_scan": self.last_scan, "notifications": len(self.rows),
            "worker_pid": self.worker.pid if self.worker else None, "restarts": self.restarts,
        }
        try:
            atomic_json(LOGS / "health.json", data)
            return True
        except OSError as exc:
            try:
                self.log({"type": "reader_health_write_error", "error": str(exc)})
            except OSError:
                pass
            return False

    def start_worker(self):
        self.reader_recovery.started()
        try:
            self.worker = subprocess.Popen(
                command("notification_backend"), cwd=ROOT,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except OSError as exc:
            self.recover(f"Avvio non riuscito: {exc}")
            return
        self.last_message = time.monotonic()
        worker = self.worker
        def read():
            for line in worker.stdout:
                try:
                    data = json.loads(line)
                except ValueError:
                    data = {"type": "item_error", "message": line[:500]}
                self.queue.put((worker.pid, data))
            self.queue.put((worker.pid, {"type": "ended"}))
        threading.Thread(target=read, daemon=True).start()

    def stop_worker(self):
        worker, self.worker = self.worker, None
        if worker and worker.poll() is None:
            from sentinel_launcher import stop_tree
            stop_tree(worker)
        self.last_message = None

    def recover(self, reason):
        self.stop_worker()
        if self.monitoring:
            self.restarts += 1
            delay = self.reader_recovery.failed(time.monotonic())
            self.set_status("starting", f"{reason} Nuovo tentativo tra {delay} secondi.")

    def add(self, data):
        notification = Notification(**data)
        if not after_cleanup(notification,getattr(self,'notifications_cleared_at',0)):return
        if not self.dedup.add(notification):
            return
        kind = classify(notification.title, notification.body)
        tags = []
        if kind == "Possibile approvazione":
            tags.append("approval")
        if notification.source != "windows":
            tags.append("test")
        try:
            timestamp = datetime.fromisoformat(notification.created).astimezone().strftime("%d/%m %H:%M:%S")
        except ValueError:
            timestamp = notification.created[:20]
        sender = "Codex" if notification.app_id.startswith("OpenAI.Codex_") else notification.app
        row = self.tree.insert("", 0, values=(timestamp, sender, kind, notification.title), tags=tuple(tags))
        self.rows[row] = notification
        if hasattr(self, 'filter_notifications'):
            self.filter_notifications()
        self.counter.set(f"{len(self.rows)} notifiche rilevate")
        self.log({"type": "notification", "classification": kind, **data})
        if not self.tree.selection():
            self.tree.selection_set(row)
            self.tree.see(row)

    def poll(self):
        try:
            self.poll_once()
            self.last_poll_success = time.time()
        except Exception as exc:
            # Tk discards a callback after an exception. Always schedule the
            # next turn; repeated failures expire the functional heartbeat.
            try:
                self.log({"type": "poll_error", "error": f"{type(exc).__name__}: {exc}"})
            except OSError:
                pass
        finally:
            self.root.after(500, self.poll)

    def poll_once(self):
        profile_request = LOGS / "profile-request.json"
        if getattr(self, "gui_profiler", None) is None and profile_request.exists():
            try:
                seconds = float(json.loads(profile_request.read_text())["seconds"])
                if not 1 <= seconds <= 30:
                    raise ValueError("Profilo GUI: durata consentita da 1 a 30 secondi")
                profile_request.unlink()
                import cProfile
                self.gui_profiler = cProfile.Profile()
                self.gui_profiler.enable()
                self.root.after(int(seconds * 1000), self.finish_gui_profile)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self.log({"type": "profile_request_error", "error": str(exc)})
        if time.monotonic() - self.last_health_read > 2:
            self.last_health_read = time.monotonic()
            request_path = LOGS / "show-window.json"
            if request_path.exists():
                try:
                    request_path.unlink()
                    self.root.deiconify()
                    self.root.lift()
                except OSError:
                    pass
            try:
                health = json.loads((LOGS / "supervisor-health.json").read_text(encoding="utf-8"))
                self.refresh_dashboard(health=health)
            except (OSError, ValueError):
                health = {}
            self.check_supervisor(health)
        try:
            for _ in range(200):
                pid, record = self.queue.get_nowait()
                if not self.worker or pid != self.worker.pid:
                    continue
                self.last_message = time.monotonic()
                kind = record.get("type")
                if kind == "status":
                    self.set_status(record["state"], record["message"])
                    if record["state"] == "blocked":
                        self.monitoring = False
                        self.stop_worker()
                elif kind == "heartbeat":
                    self.reader_recovery.healthy(time.monotonic())
                elif kind == "snapshot":
                    self.last_scan = datetime.now().astimezone().isoformat()
                    for item in record["notifications"]:
                        self.add(item)
                    self.write_health()
                elif kind == "ended":
                    if self.worker.poll() == 3:
                        self.monitoring = False
                        self.stop_worker()
                        self.set_status("blocked", "Windows non consente la lettura delle notifiche. Nessun nuovo tentativo automatico.")
                    else:
                        self.recover("Il lettore si è interrotto.")
                elif kind == "item_error":
                    self.log(record)
        except queue.Empty:
            pass
        if self.last_message and self.worker and time.monotonic() - self.last_message > 20:
            self.recover("Nessuna risposta dal lettore per 20 secondi.")
        if self.monitoring and not self.worker and self.reader_recovery.due(time.monotonic()):
            self.start_worker()

    def finish_gui_profile(self):
        if getattr(self, "gui_profiler", None) is None:
            return
        profiler, self.gui_profiler = self.gui_profiler, None
        profiler.disable()
        profiler.dump_stats(str(LOGS / "gui-profile.pstats"))
        self.log({"type": "gui_profile_completed", "file": "gui-profile.pstats"})

    def show_details(self, text):
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.insert("1.0", text)
        self.details.configure(state="disabled")

    def select(self, _=None):
        selected = self.tree.selection()
        if selected and selected[0] in self.rows:
            n = self.rows[selected[0]]
            self.show_details(f"{n.app} · {n.created} · origine: {n.source}\n{n.title}\n\n{n.body}")

    def close(self):
        if hasattr(self,'may_close') and not self.may_close():return
        self.finish_gui_profile()
        from sentinel_launcher import atomic_json
        atomic_json(LOGS / "exit-intent.json", {"run_id": self.run_id, "pid": os.getpid(), "closed_at": time.time()})
        self.monitoring = False
        self.stop_worker()
        if self.supervisor:
            import psutil
            try:
                parent = psutil.Process(self.supervisor.pid)
                for child in parent.children(recursive=True):
                    child.terminate()
                parent.terminate()
                self.supervisor.wait(timeout=2)
            except (psutil.Error, subprocess.TimeoutExpired):
                pass
        self.current_state = "closed"
        self.write_health()
        self.log({"type": "closed"})
        self.root.destroy()
        self.diagnostics.close()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    from config_store import ensure_local_files
    ensure_local_files(ROOT)
    # One monitor owns the reader and responder: a second launch exits quietly.
    import msvcrt
    LOGS.mkdir(exist_ok=True)
    lock_path = LOGS / "monitor.lock"
    instance_lock = lock_path.open("a+b")
    if lock_path.stat().st_size == 0:
        instance_lock.write(b"0")
        instance_lock.flush()
    instance_lock.seek(0)
    try:
        msvcrt.locking(instance_lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        raise SystemExit(0)
    Monitor().run()
