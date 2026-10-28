from runtime_paths import command
"""Sentinella's editable timers. UI callbacks never wait for desktop scans."""
import json
import queue
import subprocess
import threading
import time
import uuid
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox
from message_engine import ROOT, DEFAULT_MESSAGE, load_jobs, save_jobs, validate_job, TimerStore
from automation_policy import load_config, action_block_reason
from switch_control import Switch
from chat_catalog import filter_chats, project_name, state_name
from schedule_display import schedule_display, clock_label
from ui_components import AutoScrollbar


class MessagesPanel(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, padding=15)
        self.selected_job = None
        self.target = None
        self.targets = []
        self.chats = {}
        self.catalog = []
        self.scan_errors = []
        self.filter_pending = None
        self.results = queue.Queue()
        self.scan_running = False
        self.last_render = 0
        self.last_preview = 0
        self.rendering_chats = False
        self.editor_step = 1
        self.editor_baseline=None
        self.status = tk.StringVar(value="Scegli Nuovo messaggio per programmare un invio.")
        self.catalog_status=tk.StringVar(value='Premi Aggiorna elenco per rilevare le chat aperte.')
        self.list_view = ttk.Frame(self)
        self.editor = ttk.Frame(self)
        self.editor_visible = False
        self.list_view.pack(fill="both", expand=True)
        heading = ttk.Frame(self.list_view)
        heading.pack(fill="x", pady=(0, 12))
        ttk.Label(heading, text="I tuoi messaggi", style="Section.TLabel").pack(side="left")
        ttk.Button(heading, text="Nuovo messaggio", command=self.new, style="Primary.TButton").pack(side="right")
        ttk.Label(self.list_view, text="Vedi quando partiranno e quando sono stati inviati. Doppio clic per modificare.", style="Muted.TLabel").pack(anchor="w", pady=(0, 10))
        self.job_list = ttk.Treeview(self.list_view, columns=("title", "state", "next", "activity", "last"), show="headings", height=5, selectmode="extended")
        for col, label, width in (("title", "Destinatario", 190), ("state", "Stato", 85), ("next", "Prossimo invio", 180), ("activity", "Cosa sta facendo", 280), ("last", "Ultimo invio", 100)):
            self.job_list.heading(col, text=label)
            self.job_list.column(col, width=width, minwidth=60)
        self.job_list.pack(fill="both", expand=True)
        for tag, color in (("Attivo", "#19553a"), ("In pausa", "#697386"), ("Completato", "#697386")):
            self.job_list.tag_configure(tag, foreground=color)
        self.job_list.bind("<<TreeviewSelect>>", self.choose_job)
        self.job_list.bind("<Double-1>", lambda _: self.edit_selected())
        controls = ttk.Frame(self.list_view)
        controls.pack(fill="x", pady=(12, 8))
        self.edit_button = ttk.Button(controls, text="Modifica messaggio", command=self.edit_selected, state="disabled")
        self.edit_button.pack(side="left")
        self.toggle_job = ttk.Button(controls, text="Metti in pausa", command=self.toggle_selected, state="disabled")
        self.toggle_job.pack(side="left", padx=8)
        more = ttk.Menubutton(controls, text="Altre azioni")
        menu = tk.Menu(more, tearoff=False)
        menu.add_command(label="Metti in pausa tutti i messaggi", command=self.pause_all)
        more.configure(menu=menu)
        more.pack(side="right")
        ttk.Button(controls, text="Registro invii", command=self.show_history).pack(side="right", padx=8)
        self.job_detail = tk.StringVar(value="Seleziona un messaggio per leggere il testo e il motivo di un eventuale rinvio.")
        ttk.Label(self.list_view, textvariable=self.job_detail, wraplength=980, style="Muted.TLabel").pack(anchor="w", pady=(2, 4))
        top = ttk.Frame(self.editor)
        top.pack(fill="x", pady=(0, 14))
        self.back_button = ttk.Button(top, text="← Messaggi", command=self.editor_back)
        self.back_button.pack(side="left")
        self.editor_title = tk.StringVar(value="1 · Scegli le chat")
        ttk.Label(top, textvariable=self.editor_title, style="Section.TLabel").pack(side="left", padx=15)
        self.commit_button=ttk.Button(top,text='Attiva programmazione',command=self.activate,style='Primary.TButton')
        self.pick_page = ttk.Frame(self.editor)
        self.write_page = ttk.Frame(self.editor)
        self.pick_page.pack(fill="both", expand=True)
        filters = ttk.Frame(self.pick_page)
        filters.pack(fill="x", pady=(0, 8))
        self.search = tk.StringVar()
        self.app_filter = tk.StringVar(value="Tutte")
        self.project_filter = tk.StringVar(value="Tutti")
        self.state_filter = tk.StringVar(value="Tutti")
        self.period_filter = tk.StringVar(value="Ultimi 7 giorni")
        def filter_field(column, label, weight=1):
            filters.columnconfigure(column, weight=weight)
            frame = ttk.Frame(filters)
            frame.grid(row=0, column=column, sticky="ew", padx=(0, 10 if column<4 else 0))
            ttk.Label(frame, text=label, style="Muted.TLabel").pack(anchor="w", pady=(0, 5))
            return frame
        ttk.Entry(filter_field(0, "Cerca", 3), textvariable=self.search, width=13).pack(fill="x")
        ttk.Combobox(filter_field(1, "Applicazione"), textvariable=self.app_filter,
                     values=("Tutte", "Codex", "Claude"), state="readonly", width=7).pack(fill="x")
        ttk.Combobox(filter_field(2, "Periodo"), textvariable=self.period_filter,
                     values=("Ultime 24 ore", "Ultimi 7 giorni", "Ultimi 30 giorni", "Tutte"), state="readonly", width=12).pack(fill="x")
        self.project_options = ttk.Combobox(filter_field(3, "Progetto", 2), textvariable=self.project_filter,
                                            values=("Tutti",), state="readonly", width=11)
        self.project_options.pack(fill="x")
        self.state_options = ttk.Combobox(filter_field(4, "Stato", 2), textvariable=self.state_filter,
                                          values=("Tutti",), state="readonly", width=12)
        self.state_options.pack(fill="x")
        for variable in (self.search, self.app_filter, self.project_filter, self.state_filter, self.period_filter):
            variable.trace_add("write", self.filter_changed)
        chat_table = ttk.Frame(self.pick_page)
        chat_table.pack(fill="both", expand=True)
        self.chat_list = ttk.Treeview(chat_table, columns=("app", "title", "session", "group", "state", "activity"), show="headings", height=6, selectmode="extended")
        chat_scroll = AutoScrollbar(chat_table, orient="vertical", command=self.chat_list.yview)
        self.chat_list.configure(yscrollcommand=chat_scroll.set)
        for col, label, width in (("app", "App", 60), ("title", "Chat", 230), ("session", "ID sessione", 100), ("group", "Progetto", 160), ("state", "Stato", 150), ("activity", "Ultima attività", 110)):
            self.chat_list.heading(col, text=label, anchor='w')
            self.chat_list.column(col, width=width, minwidth=60)
        chat_scroll.pack(side="right", fill="y")
        self.chat_list.pack(side="left", fill="both", expand=True)
        self.empty_chats = ttk.Label(chat_table, text="Nessuna chat con questi filtri.\nScegli Tutte per includere quelle senza data disponibile.", style="Muted.TLabel", justify="center", wraplength=430)
        self.chat_list.bind("<<TreeviewSelect>>", self.choose_chat)
        self.chat_list.bind("<Button-1>", self.click_chat)
        self.chat_list.bind("<space>", lambda _: self.toggle_chat_row(self.chat_list.focus()))
        self.target_label = tk.StringVar(value="Nessuna chat selezionata")
        selected_bar = ttk.Frame(self.pick_page)
        selected_bar.pack(fill="x", pady=(12, 0))
        ttk.Label(selected_bar, textvariable=self.target_label, wraplength=740).pack(side="left", fill="x", expand=True)
        self.clear_targets_button=ttk.Button(selected_bar,text='Svuota selezione',command=self.clear_targets,state='disabled')
        self.clear_targets_button.pack(side='right')
        self.refresh_button = ttk.Button(selected_bar, text="Aggiorna elenco", command=self.refresh)
        self.refresh_button.pack(side='right', padx=(8,8))
        self.continue_button = ttk.Button(top, text="Continua (0) →", command=self.next_step, style="Primary.TButton", state="disabled")
        self.continue_button.pack(side='right')
        self.recipient_summary = tk.StringVar()
        self.recipient_label=ttk.Label(self.write_page, textvariable=self.recipient_summary, wraplength=980)
        self.recipient_label.pack(anchor="w", pady=(0, 14))
        self.composer_label=ttk.Label(self.write_page, text="Cosa vuoi scrivere?", style="Section.TLabel")
        self.composer_label.pack(anchor="w")
        self.message = tk.Text(self.write_page, width=1, height=7, wrap="word", font=("Segoe UI", 11), undo=True, relief="flat", padx=12, pady=12)
        self.message.insert("1.0", DEFAULT_MESSAGE)
        self.message.pack(fill="both", expand=True, pady=(8, 18))
        timing = ttk.Frame(self.write_page)
        timing.pack(fill="x")
        ttk.Label(timing, text="Intervallo").pack(side="left")
        self.minutes = tk.StringVar(value="15")
        ttk.Combobox(timing, values=(5, 10, 15, 30, 60), width=7, textvariable=self.minutes).pack(side="left", padx=8)
        ttk.Label(timing, text="minuti").pack(side="left")
        self.repeat = tk.BooleanVar(value=True)
        Switch(timing, text="Ripeti nel tempo", variable=self.repeat).pack(side="left", padx=24)
        self.send_when_active = tk.BooleanVar(value=False)
        Switch(timing, text="Invia anche mentre lavora", variable=self.send_when_active).pack(side="left", padx=8)
        self.send_when_present = tk.BooleanVar(value=False)
        self.preview = tk.StringVar()
        self.preview_label=ttk.Label(self.write_page, textvariable=self.preview, wraplength=980)
        self.preview_label.pack(anchor="w", pady=(14, 8))
        ttk.Label(self.write_page, text="Le bozze restano protette. L'opzione vale per tutti i destinatari: il messaggio può essere ricevuto o accodato durante il lavoro. Gli orari seguono il PC.", style="Muted.TLabel", wraplength=980).pack(anchor="w")
        ttk.Button(self.write_page, text="Attiva programmazione", command=self.activate, style="Primary.TButton").pack(anchor="e", pady=(16, 0))
        for variable in (self.minutes, self.repeat, self.send_when_active, self.send_when_present):
            variable.trace_add("write", lambda *_: self.update_preview())
        self.status_label=ttk.Label(self, textvariable=self.status, style="Muted.TLabel", wraplength=980)
        self.status_label.pack(side="bottom", fill="x", pady=(12, 0), before=self.list_view)
        from workspace_ui import modernize_messages
        modernize_messages(self)
        self.editor_baseline=self.editor_signature()
        self.render_jobs()
        if len(self.job_list.get_children()) == 1:
            self.job_list.selection_set(self.job_list.get_children()[0])
        self.after(500, self.poll)

    def show_editor(self):
        self.list_view.pack_forget()
        self.editor.pack(fill="both", expand=True)
        self.editor_visible = True
        self.show_pick()
        self.refresh()

    def use_saved_message(self, text):
        current = self.message.get('1.0', 'end-1c')
        if current.strip() and current != text and current != DEFAULT_MESSAGE:
            if not messagebox.askyesno('Sostituire il testo nell’editor?',
                                      'Nell’editor è già presente un testo diverso. Vuoi sostituirlo con il messaggio salvato? Nessun invio partirà da questa scelta.', parent=self):
                return False
        self.message.delete('1.0', 'end')
        self.message.insert('1.0', text)
        if not self.editor_visible:self.show_editor()
        if self.targets:self.next_step()
        self.status.set('Messaggio riutilizzato nell’editor. Controlla destinatari e opzioni prima di salvare la programmazione.')
        return True

    def show_list(self):
        self.editor.pack_forget()
        self.list_view.pack(fill="both", expand=True)
        self.editor_visible = False

    def show_pick(self):
        self.commit_button.pack_forget()
        self.write_page.pack_forget()
        self.pick_page.pack(fill="both", expand=True)
        self.editor_step = 1
        self.editor_title.set("1 · Scegli le chat")
        self.back_button.configure(text="← Messaggi")
        self.continue_button.pack(side='right')
        self.continue_button.configure(text=f'Continua ({len(self.targets)}) →')
        self.status.set('Scegli una o più chat, poi premi Continua. Clicca una riga per aggiungerla o toglierla; le selezioni restano conservate quando cambi i filtri.')

    def editor_back(self):
        self.show_pick() if self.editor_step == 2 else self.show_list()

    def next_step(self):
        if not self.targets:
            self.status.set("Scegli almeno una chat per continuare.")
            return
        self.pick_page.pack_forget()
        self.write_page.pack(fill="both", expand=True)
        self.editor_step = 2
        self.editor_title.set("2 · Scrivi e programma")
        self.back_button.configure(text="← Destinatari")
        self.continue_button.pack_forget()
        self.commit_button.configure(text='Salva modifiche' if self.selected_job else 'Attiva programmazione')
        self.commit_button.pack(side='right')
        names = "; ".join(f"{r['app'].title()} · {r['title']}" for r in self.targets)
        self.recipient_summary.set(f"Destinatari ({len(self.targets)}): {names}")
        self.update_preview()
        self.status.set('Modifica il testo e le opzioni, poi salva in alto a destra.' if self.selected_job else 'Controlla testo e opzioni, poi attiva in alto a destra.')

    def update_preview(self):
        self.last_preview = time.monotonic()
        try:
            minutes = float(self.minutes.get().replace(",", "."))
            if not 1 <= minutes <= 10080:
                raise ValueError()
            when = clock_label(time.time() + minutes * 60, time.time())
            repetition = f"ogni {minutes:g} minuti" if self.repeat.get() else "una sola volta"
            previous=next((job for job in load_jobs() if job['id']==self.selected_job),None)
            text=(f"{len(self.targets)} chat · {repetition}. La programmazione resta in pausa dopo il salvataggio." if previous and not previous.get('enabled') else
                  f"{len(self.targets)} chat · {repetition}. Le modifiche ai tempi o al testo ricalcolano il prossimo invio dal salvataggio." if previous else
                  f"{len(self.targets)} chat · {repetition}. Primo invio tra {minutes:g} min dal salvataggio, alle {when}.")
            if getattr(self, 'send_when_present', None) is not None and self.send_when_present.get():
                text += ' Invio in background consentito durante l’uso del PC.'
            if load_config().get("paused"):
                text += " Sentinella è in pausa: gli invii aspettano la ripresa."
            if self.preview.get() != text:
                self.preview.set(text)
        except (ValueError, OSError):
            self.preview.set("Scegli un intervallo da 1 minuto a 7 giorni.")

    def click_chat(self, event):
        if self.chat_list.identify_region(event.x, event.y) != "cell":
            return
        return self.toggle_chat_row(self.chat_list.identify_row(event.y))

    def toggle_chat_row(self, key):
        if key and key in self.chats:
            self.chat_list.focus_set()
            self.chat_list.focus(key)
            if key in self.chat_list.selection():
                self.chat_list.selection_remove(key)
            else:
                self.chat_list.selection_add(key)
            self.choose_chat()
        return "break"

    def clear_targets(self):
        self.targets = []
        self.rendering_chats = True
        self.chat_list.selection_remove(self.chat_list.selection())
        self.rendering_chats = False
        self.update_target_summary()

    def update_target_summary(self):
        names = "; ".join(f"{r['app'].title()} · {r['title']}" for r in self.targets)
        self.target_label.set(f"{len(self.targets)} chat selezionate: {names[:200]}" if self.targets else "Nessuna chat selezionata")
        self.continue_button.configure(text=f'Continua ({len(self.targets)}) →',state="normal" if self.targets and not any(row.get('ambiguous') for row in self.targets) else "disabled")
        self.clear_targets_button.configure(state='normal' if self.targets else 'disabled')

    def edit_selected(self):
        selected = self.job_list.selection()
        if not selected:
            return
        job = next((j for j in load_jobs() if j["id"] == selected[0]), None)
        if not job:
            return
        if self.editor_baseline is not None and self.editor_signature()!=self.editor_baseline:
            if self.selected_job==job['id']:
                self.list_view.pack_forget();self.editor.pack(fill='both',expand=True);self.editor_visible=True;self.next_step();return
            if not messagebox.askyesno('Modifiche non salvate','Scartare le modifiche nell’editor e aprire questa programmazione?',parent=self):return
        self.selected_job = job["id"]
        self.targets = [job]
        self.target = job
        self.message.delete("1.0", "end")
        self.message.insert("1.0", job["message"])
        self.minutes.set(f"{job['interval_seconds'] / 60:g}")
        self.repeat.set(job.get("repeat", True))
        self.send_when_active.set(job.get("send_when_active", False))
        self.send_when_present.set(job.get('send_when_present', True))
        self.editor_baseline=self.editor_signature()
        self.update_target_summary()
        self.show_editor()
        self.next_step()

    def toggle_selected(self):
        selected = self.job_list.selection()
        jobs = [job for job in load_jobs() if job["id"] in selected]
        if not jobs:
            self.status.set("Seleziona uno o più messaggi nell'elenco.")
            return
        self.set_enabled(not all(job.get("enabled") for job in jobs))

    def refresh(self):
        if self.scan_running:
            return
        self.scan_running = True
        self.refresh_button.configure(state="disabled")
        self.catalog_status.set("Caricamento delle chat nelle applicazioni aperte…")
        self.empty_chats.configure(text='Caricamento delle chat…')
        if not self.catalog:self.empty_chats.place(relx=.5,rely=.5,anchor='center')
        def scan():
            rows, errors = [], []
            for app in ("codex", "claude"):
                try:
                    from bounded_process import run_bounded
                    process = run_bounded(command("chat_desktop", "--app", app), cwd=ROOT, timeout=12)
                    if process["timed_out"]:
                        raise TimeoutError("Ricerca chat interrotta dopo 12 secondi")
                    result = json.loads(process["stdout"].strip().splitlines()[-1])
                    if not isinstance(result,dict) or not isinstance(result.get('chats',[]),list):raise ValueError('Risposta elenco chat non valida')
                    for row in result.get('chats',[]):
                        if not isinstance(row,dict) or any(not isinstance(row.get(key),str) for key in ('app','title','group')):
                            raise ValueError('Identità chat incompleta nella risposta')
                        rows.append(row)
                    if result.get("error"):
                        errors.append(result["error"])
                    if result.get("metadata_warning"):
                        errors.append(result["metadata_warning"])
                except Exception as exc:
                    errors.append(f"{app}: {exc}")
            self.results.put((rows, errors))
        try:threading.Thread(target=scan, daemon=True).start()
        except RuntimeError as exc:
            self.scan_running=False;self.refresh_button.configure(state='normal',text='Riprova')
            self.catalog_status.set(f'Impossibile avviare la ricerca: {exc}. Premi Riprova.')

    def filter_changed(self, *_):
        if self.filter_pending:
            self.after_cancel(self.filter_pending)
        self.filter_pending = self.after(180, self.apply_filters)

    @staticmethod
    def identity(row):
        return row["app"], row["title"], row["group"]

    def apply_filters(self, wanted=None):
        self.filter_pending = None
        days = {"Ultime 24 ore": 1, "Ultimi 7 giorni": 7, "Ultimi 30 giorni": 30, "Tutte": 0}[self.period_filter.get()]
        rows = filter_chats(self.catalog, self.search.get(), self.app_filter.get(), self.project_filter.get(), self.state_filter.get(), days)
        selected_ids = {self.identity(r) for r in self.targets}
        self.rendering_chats = True
        try:
            self.chat_list.delete(*self.chat_list.get_children())
            self.chats = {}
            selected = []
            for index, row in enumerate(rows):
                key = str(index)
                self.chats[key] = row
                date = row.get("last_activity_at")
                date_text = datetime.fromtimestamp(date).strftime("%d/%m %H:%M") if date else "—"
                session = row.get('session_id') or row.get('thread_id') or ('Non univoco' if row.get('ambiguous') else '—')
                self.chat_list.insert("", "end", iid=key, values=(row["app"].title(), row["title"], session[:12], row.get("project") or project_name(row["group"]), state_name(row), date_text))
                if self.identity(row) in selected_ids:
                    selected.append(key)
            if selected:
                self.chat_list.selection_set(selected)
        finally:
            self.rendering_chats = False
        if rows:
            self.empty_chats.place_forget()
        else:
            self.empty_chats.configure(text='Elenco non disponibile. Premi Riprova per aggiornare.' if self.scan_errors and not self.catalog else 'Nessun risultato: modifica i filtri o premi Aggiorna.\nLe chat già selezionate restano selezionate anche cambiando filtro.')
            self.empty_chats.place(relx=0.5, rely=0.5, anchor="center")
        self.update_target_summary()
        missing = sum(row.get("last_activity_at") is None for row in self.catalog)
        text = f"{len(rows)} di {len(self.catalog)} chat · {self.period_filter.get()}"
        if missing and days:
            text += f" · {missing} senza data, visibili con Tutte"
        if any(row.get('ambiguous') for row in rows):
            text += ' · Nome duplicato: rinomina la chat nell’app, poi aggiorna l’elenco'
        self.catalog_status.set(text + (" · " + " · ".join(self.scan_errors) if self.scan_errors else ""))

    def choose_chat(self, _=None):
        if self.rendering_chats:
            return
        visible = {self.identity(row) for row in self.chats.values()}
        chosen = {self.identity(r): r for r in self.targets if self.identity(r) not in visible}
        for key in self.chat_list.selection():
            if key in self.chats:
                row = self.chats[key]
                chosen[self.identity(row)] = row
        self.targets = list(chosen.values())
        self.target = self.targets[0] if self.targets else None
        self.update_target_summary()

    def new(self):
        if self.editor_baseline is not None and self.editor_signature()!=self.editor_baseline:
            if not messagebox.askyesno('Nuova programmazione','Ci sono modifiche non salvate nell’editor. Scartarle e iniziare una nuova programmazione?',parent=self):return False
        self.selected_job = None
        self.job_list.selection_remove(self.job_list.selection())
        self.clear_targets()
        self.message.delete("1.0", "end")
        self.message.insert("1.0", DEFAULT_MESSAGE)
        self.minutes.set("15")
        self.repeat.set(True)
        self.send_when_active.set(False)
        self.send_when_present.set(False)
        self.status.set("Scegli le chat, poi premi Continua.")
        self.editor_baseline=self.editor_signature()
        self.show_editor()
        return True

    def editor_signature(self):
        return (self.message.get('1.0','end-1c'),self.minutes.get(),self.repeat.get(),self.send_when_active.get(),self.send_when_present.get(),tuple(sorted(self.identity(row) for row in self.targets)))

    def activate(self):
        try:
            if not self.targets or any(row.get("ambiguous") for row in self.targets):
                raise ValueError("Scegli almeno una chat senza nomi duplicati. Se il nome è duplicato, rinomina la chat nell’app e aggiorna l’elenco.")
            jobs = load_jobs()
            saved = []
            preserved_schedule = False
            for target in self.targets:
                previous = next((j for j in jobs if (j['app'], j['title'], j['group']) == (target['app'], target['title'], target['group'])), None)
                job_id = previous["id"] if previous else str(uuid.uuid4())
                # Re-selecting a sidebar row must not erase its pinned thread
                # identity, transport, or explicit send-while-active policy.
                editing_existing=bool(previous and self.selected_job==previous['id'])
                job = {**(previous or {}), **target, "id": job_id, "version": str(uuid.uuid4()), "enabled": bool(previous.get('enabled')) if editing_existing else True, "activated_at": time.time(), "interval_seconds": float(self.minutes.get().replace(",", ".")) * 60, "message": self.message.get("1.0", "end-1c"), "repeat": self.repeat.get()}
                if job["app"] == "codex":
                    if not job.get("thread_id"):
                        raise ValueError("Aggiorna l'elenco per verificare l'identità della chat prima di programmare l'invio.")
                    job["transport"] = "codex_app"
                active_option = getattr(self, "send_when_active", None)
                if active_option is not None:
                    job["send_when_active"] = bool(active_option.get())
                present_option = getattr(self, 'send_when_present', None)
                if present_option is not None:
                    job['send_when_present'] = bool(present_option.get())
                if previous and all(job.get(k) == previous.get(k) for k in ['message', 'interval_seconds', 'repeat', 'app', 'title', 'group', 'thread_id', 'transport']):
                    job['version'] = previous['version']
                    job['activated_at'] = previous['activated_at']
                    preserved_schedule = True
                validate_job(job)
                jobs = [j for j in jobs if j["id"] != job["id"]]
                jobs.append(job)
                saved.append(job_id)
            save_jobs(jobs)
            self.selected_job = saved[0] if len(saved) == 1 else None
            self.status.set(f"{len(saved)} programmazioni salvate. " + ("Sentinella è in pausa globale." if load_config().get("paused") else "Opzioni aggiornate; scadenza mantenuta." if preserved_schedule else "Primo invio dopo l'intervallo scelto."))
            store = TimerStore(ROOT / "logs" / "messages.sqlite3")
            try:
                for item in jobs:
                    if item["id"] in saved:
                        store.prepare(item, time.time())
            finally:
                store.db.close()
            self.render_jobs()
            self.editor_baseline=self.editor_signature()
            self.show_list()
        except (OSError, ValueError) as exc:
            self.status.set(str(exc))

    def choose_job(self, _=None):
        selected = self.job_list.selection()
        jobs = load_jobs()
        active = bool(selected) and all(j.get("enabled") for j in jobs if j["id"] in selected)
        self.toggle_job.configure(text="Metti in pausa" if active else "Riprendi messaggio", state="normal" if selected else "disabled")
        self.edit_button.configure(state="normal" if len(selected) == 1 else "disabled")
        job = next((j for j in jobs if selected and j["id"] == selected[0]), None)
        if job:
            state = self.display_states.get(job["id"], {})
            options = 'Anche mentre la chat lavora' if job.get('send_when_active') else 'Quando la chat è libera'
            options += ' · Anche durante l’uso del PC (in background)' if job.get('send_when_present', True) else ' · Dopo l’inattività del PC'
            self.job_detail.set(f"{job['title']} · {state.get('status','')}\n{state.get('frequency', '')} · Scadenza prevista: {state.get('scheduled_at', '—')}\n{options}\n{state.get('detail', '')}\n\n{job['message']}")
        if hasattr(self, 'detail_text'):
            detail = self.job_detail.get() if job else 'Seleziona una programmazione per leggere testo, frequenza e motivo del rinvio.'
            if detail != getattr(self, 'detail_value', None):
                self.detail_value = detail
                self.detail_text.configure(state='normal'); self.detail_text.delete('1.0', 'end')
                self.detail_text.insert('1.0', detail)
                self.detail_text.configure(state='disabled')

    def toggle_job_id(self, job_id):
        last_id, last_at = getattr(self, '_last_row_toggle', (None, 0))
        now = time.monotonic()
        if last_id == job_id and now - last_at < .5:
            return
        job = next((item for item in load_jobs() if item['id'] == job_id), None)
        if job is None:
            return
        self._last_row_toggle = (job_id, now)
        self.set_enabled(not job.get('enabled', False), job_ids=(job_id,))

    def edit_job_id(self, job_id):
        self.render_jobs()
        if job_id not in self.job_list.get_children():
            return
        self.job_list.selection_set(job_id)
        self.job_list.focus(job_id)
        self.edit_selected()

    def set_enabled(self, enabled, job_ids=None):
        selected = job_ids if job_ids is not None else self.job_list.selection()
        if not selected:
            self.status.set("Seleziona un messaggio nell'elenco")
            return
        jobs = load_jobs()
        store = TimerStore(ROOT / 'logs/messages.sqlite3')
        try:
            for job in jobs:
                if job['id'] in selected and bool(job.get('enabled')) != bool(enabled):
                    job['enabled'] = enabled
                    store.event(job, "Ripreso" if enabled else "In pausa", "Programmazione ripresa senza azzerare scadenza o prenotazioni" if enabled else "Nessun altro invio finché resta in pausa")
        finally:
            store.db.close()
        save_jobs(jobs)
        self.render_jobs()

    def pause_all(self):
        jobs = load_jobs()
        for job in jobs:
            job["enabled"] = False
        save_jobs(jobs)
        self.render_jobs()

    def render_jobs(self):
        self.last_render = time.monotonic()
        jobs = load_jobs()
        store = TimerStore(ROOT / "logs" / "messages.sqlite3")
        states = {r["id"]: r for r in store.rows()}
        store.db.close()
        existing = set(self.job_list.get_children())
        config = load_config()
        waiting_reason = action_block_reason(config)
        now = time.time()
        self.display_states = {}
        for job in jobs:
            display = schedule_display(job, states.get(job["id"]), config, now, waiting_reason)
            self.display_states[job["id"]] = display
            values = ('Ⅱ Pausa' if job.get('enabled') else '▶ Riprendi', job["title"], display["status"], display["next_precise"], display["last_sent"])
            if job["id"] in existing:
                if tuple(self.job_list.item(job["id"], "values")) != values:
                    self.job_list.item(job["id"], values=values, tags=(display["activation"],))
                existing.remove(job["id"])
            else:
                self.job_list.insert("", "end", iid=job["id"], values=values, tags=(display["activation"],))
        for key in existing:
            self.job_list.delete(key)
        self.choose_job()

    def poll(self):
        received=False
        try:
            rows, errors = self.results.get_nowait()
            received=True
            wanted = {(r["app"], r["title"], r["group"]) for r in self.targets}
            self.catalog = rows
            self.scan_errors = errors
            projects = ["Tutti", *sorted({row.get("project") or project_name(row["group"]) for row in rows}, key=str.casefold)]
            states = ["Tutti", *sorted({state_name(row) for row in rows}, key=str.casefold)]
            self.project_options.configure(values=projects)
            self.state_options.configure(values=states)
            if self.project_filter.get() not in projects:
                self.project_filter.set("Tutti")
            if self.state_filter.get() not in states:
                self.state_filter.set("Tutti")
            self.apply_filters()
            self.scan_running = False
            self.refresh_button.configure(state="normal")
        except queue.Empty:
            pass
        except Exception as exc:
            self.scan_errors=[f'Elenco chat: {exc}']
            self.catalog_status.set(f'Impossibile mostrare le chat: {exc}. Premi Riprova.')
            self.empty_chats.configure(text='Elenco non disponibile. Premi Riprova per aggiornare.')
            self.empty_chats.place(relx=.5,rely=.5,anchor='center')
        finally:
            # Rendering errors must never strand the loading state or stop polling.
            if received:
                self.scan_running=False
                self.refresh_button.configure(state='normal',text='Riprova' if self.scan_errors else 'Aggiorna elenco')
        try:
            if self.winfo_ismapped() and not self.editor_visible and time.monotonic() - self.last_render >= 1:
                self.render_jobs()
            elif self.winfo_ismapped() and self.editor_visible and self.editor_step == 2 and time.monotonic() - self.last_preview >= 5:
                self.update_preview()
        except Exception as exc:
            self.status.set(f'Aggiornamento non riuscito: {exc}. Puoi riprovare.')
        finally:
            self.after(1000, self.poll)

    def show_history(self):
        if hasattr(self, 'history_callback'):
            selected = self.job_list.selection()
            self.history_callback(selected[0] if len(selected) == 1 else None)
            return
        from workspace_ui import HistoryPanel
        window = tk.Toplevel(self)
        window.title("Registro invii · Sentinella")
        window.geometry("1040x650")
        panel = HistoryPanel(window)
        panel.pack(fill="both", expand=True)
        selected = self.job_list.selection()
        panel.open_job(selected[0] if len(selected) == 1 else None)
