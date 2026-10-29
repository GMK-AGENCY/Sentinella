"""Less frequent controls live here, away from the main pause button."""
import json
import math
from datetime import datetime
import queue
import threading
import time
from tkinter import ttk
from automation_policy import ROOT, load_config
from polling_policy import interval, validate_interval
from startup_manager import startup_enabled, set_startup
from config_store import detect_running_apps
from unsaved_changes import resolve_unsaved


def save_shared_controls(questions, approvals, recovery):
    config = load_config()
    config["shared_controls"] = {"questions": bool(questions), "approvals": bool(approvals)}
    config["enabled"] = bool(approvals)
    for options in config["apps"].values():
        options.update(questions_enabled=bool(questions), approvals_enabled=bool(approvals))
    if bool(recovery) and not config.get("automatic_file_recovery"):
        import time
        config["file_recovery_enabled_at"] = time.time()
    config["automatic_file_recovery"] = bool(recovery)
    from sentinel_launcher import atomic_json
    atomic_json(ROOT / "approval-config.json", config)
    return config


def save_settings(notifications, chat_minutes, away_mode, idle_minutes=None):
    seconds = validate_interval("notifications", notifications)
    try:
        minutes = float(str(chat_minutes).replace(",", "."))
    except ValueError:
        raise ValueError("Inserisci un numero valido per i minuti delle chat") from None
    chats = validate_interval("chats", minutes * 60)
    idle = None
    if idle_minutes is not None:
        try:
            idle = float(str(idle_minutes).replace(",", ".")) * 60
        except (TypeError, ValueError):
            raise ValueError("Inserisci un numero valido per i minuti di inattività") from None
        if not math.isfinite(idle) or not 0 <= idle <= 86400:
            raise ValueError("L'inattività deve essere compresa tra 0 e 1440 minuti")
    config = load_config()
    config.update(notification_poll_seconds=seconds, chat_scan_seconds=chats, away_mode=bool(away_mode))
    if idle is not None:
        config["idle_seconds_before_actions"] = idle
    path = ROOT / "approval-config.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return config


class SettingsPanel(ttk.Frame):
    def __init__(self, parent, on_saved, open_logs, restart_reader):
        from workspace_ui import build_settings
        build_settings(self, parent, on_saved, open_logs, restart_reader)
        self.timing_baseline = self._timing_values()
        self.saved_away = bool(self.away.get())

    def _timing_values(self):
        values = []
        for variable in (self.notifications, self.chats, self.idle):
            text = variable.get().strip()
            try:
                number = float(text.replace(',', '.'))
                values.append(number if math.isfinite(number) else text)
            except ValueError:
                values.append(text)
        return tuple(values)

    def is_dirty(self):
        return self._timing_values() != self.timing_baseline

    def discard_changes(self):
        try:
            config = load_config()
            self.notifications.set(f"{interval(config, 'notifications'):g}")
            self.chats.set(f"{interval(config, 'chats') / 60:g}")
            self.idle.set(f"{float(config.get('idle_seconds_before_actions', 30)) / 60:g}")
            self.timing_baseline = self._timing_values()
            self.saved_away = bool(config.get('away_mode', False))
            self.feedback.set('')
            return True
        except (OSError, ValueError) as exc:
            self.feedback.set(str(exc))
            return False

    def may_leave(self):
        return resolve_unsaved(self, dirty=self.is_dirty(), save=self.save, discard=self.discard_changes)

    def save(self):
        try:
            config = save_settings(self.notifications.get(), self.chats.get(), self.away.get(), self.idle.get())
            self.timing_baseline = self._timing_values()
            self.saved_away = bool(config.get('away_mode', False))
            self.feedback.set("Tempi aggiornati.")
            self.on_saved(config)
            return True
        except (ValueError, OSError) as exc:
            self.feedback.set(str(exc))
            return False

    def save_away(self):
        """Persist this immediate switch without saving pending timing edits."""
        try:
            config = load_config()
            config['away_mode'] = bool(self.away.get())
            from sentinel_launcher import atomic_json
            atomic_json(ROOT / 'approval-config.json', config)
            self.saved_away = bool(config['away_mode'])
            self.feedback.set('Risposta automatica aggiornata.')
            self.on_saved(config)
            return True
        except (OSError, ValueError) as exc:
            self.away.set(self.saved_away)
            self.feedback.set(str(exc))
            return False

    def save_commands(self):
        """Save only this opt-in, without committing pending timing edits."""
        config = load_config()
        previous = bool(config.get('automatic_command_approval', False))
        try:
            config['automatic_command_approval'] = bool(self.commands.get())
            from sentinel_launcher import atomic_json
            atomic_json(ROOT / 'approval-config.json', config)
            self.feedback.set('Approvazione dei comandi ' + ('attivata. Restano valide pausa e inattività.' if self.commands.get() else 'disattivata.'))
            self.on_saved(config)
            return True
        except (OSError, ValueError) as exc:
            self.commands.set(previous)
            self.feedback.set(str(exc))
            return False

    def save_apps(self):
        try:
            config = save_shared_controls(self.questions.get(), self.approvals.get(), self.recovery.get())
            self.feedback.set("Controlli salvati per tutte le applicazioni rilevate. Restano valide pausa e cartelle delegate.")
            self.on_saved(config)
        except (OSError, ValueError) as exc:
            self.feedback.set(str(exc))

    def detect_apps(self):
        if getattr(self,'diagnostic_busy',False):return
        self._diagnostic('Ricerca delle applicazioni in corso…',busy=True)
        results=queue.Queue()
        def detect():
            try:results.put((True,detect_running_apps()))
            except Exception as exc:results.put((False,str(exc)))
        threading.Thread(target=detect,daemon=True).start()
        deadline=time.monotonic()+15
        def poll():
            if not self.winfo_exists():return
            try:
                ok,value=results.get_nowait()
            except queue.Empty:
                if time.monotonic()<deadline:
                    self.after(100,poll);return
                self._diagnostic('Ricerca non completata entro 15 secondi. Riprova.');return
            if not ok:
                self._diagnostic(f'Rilevamento non riuscito: {value}');return
            try:
                config=load_config()
                for app,path in value.items():config['apps'][app]['process_path']=path
                from sentinel_launcher import atomic_json
                atomic_json(ROOT/'approval-config.json',config)
                names=', '.join(app.title() for app in value)
                self._diagnostic(f'Rilevamento completato: {names} ({len(value)}).' if value else 'Nessuna applicazione riconosciuta. Aprila e riprova.')
            except (OSError,ValueError) as exc:
                self._diagnostic(f'Rilevamento non salvato: {exc}')
        self.after(100,poll)

    def _diagnostic(self,text,busy=False):
        self.diagnostic_busy=busy
        self.diagnostic_feedback.set(text)
        for button in self.diagnostic_buttons:
            button.configure(state='disabled' if busy else 'normal')

    def restart_notifications(self):
        if getattr(self,'diagnostic_busy',False):return
        started=time.time()
        self._diagnostic('Riavvio del lettore… attendo una nuova lettura delle notifiche.',busy=True)
        def begin():
            try:self.reader_restart()
            except Exception as exc:
                self._diagnostic(f'Riavvio non riuscito: {exc}');return
            self.after(100,poll)
        def poll():
            if not self.winfo_exists():return
            try:
                health=json.loads((ROOT/'logs/health.json').read_text(encoding='utf-8'))
                updated=datetime.fromisoformat(health['updated_at']).timestamp()
                scan=datetime.fromisoformat(health.get('last_successful_scan') or '1970-01-01T00:00:00+00:00').timestamp()
                if updated>=started:
                    if health.get('state')=='active' and scan>=started:
                        self._diagnostic('Lettore riavviato: nuova lettura delle notifiche completata.');return
                    if health.get('state') in {'blocked','error'}:
                        self._diagnostic('Lettore non disponibile. Controlla il motivo nei registri tecnici.');return
            except (OSError,ValueError,KeyError,TypeError):pass
            if time.time()-started>=20:
                self._diagnostic('Riavvio richiesto, ma nessuna nuova lettura confermata entro 20 secondi. Controlla i registri.');return
            self.after(200,poll)
        self.after(50,begin)

    def toggle_startup(self):
        try:
            set_startup(self.startup.get())
            self.feedback.set("Avvio con Windows attivato." if self.startup.get() else "Avvio con Windows disattivato.")
        except Exception as exc:
            self.startup.set(startup_enabled())
            self.feedback.set(f"Impossibile aggiornare l'avvio automatico: {exc}")
