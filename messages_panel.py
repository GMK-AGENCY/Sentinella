from runtime_paths import command
"""Sentinella's editable timers. UI callbacks never wait for desktop scans."""
import json
import queue
import sqlite3
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
from ui_components import AutoScrollbar, TreeRowActions
from unsaved_changes import resolve_unsaved


class MessagesPanel(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, padding=20)
        self.selected_job=None;self.target=None;self.targets=[];self.chats={};self.catalog=[]
        self.scan_errors=[];self.filter_pending=None;self.results=queue.Queue();self.scan_running=False
        self.recovery_results=queue.Queue();self.recovery_running=False
        self.last_render=0;self.rendering_chats=False;self.rendering_jobs=False
        self.editor_step=1;self.editor_baseline=None;self.editor_visible=False;self.loading_job=False
        self.status=tk.StringVar()
        self.catalog_status=tk.StringVar(value='Premi Aggiorna elenco per rilevare le chat aperte.')
        self.list_view=ttk.Frame(self);self.list_view.pack(fill='both',expand=True)
        heading=ttk.Frame(self.list_view);heading.pack(fill='x',pady=(0,10))
        ttk.Label(heading,text='Programmazioni salvate',style='Section.TLabel').pack(side='left')
        ttk.Button(heading,text='+ Nuova programmazione',command=self.new,style='Primary.TButton').pack(side='right')
        self.split=ttk.Panedwindow(self.list_view,orient='vertical');self.split.pack(fill='both',expand=True)
        listing=ttk.Frame(self.split);self.split.add(listing,weight=1)
        listing.columnconfigure(0,weight=1);listing.rowconfigure(0,weight=1)
        self.job_list=ttk.Treeview(listing,columns=('app','title','state','next','last','toggle','delete'),show='headings',height=3,selectmode='browse')
        for col,label,width in [('app','App',64),('title','Destinatario',180),('state','Stato',125),('next','Prossima attività',150),('last','Ultima consegna',110),('toggle','',112),('delete','',38)]:
            self.job_list.heading(col,text=label,anchor='w')
            self.job_list.column(col,width=width,minwidth=width if col in {'app','toggle','delete'} else 70,anchor='w',stretch=col not in {'app','toggle','delete'})
        self.job_list.grid(row=0,column=0,sticky='nsew')
        for orient,axis,r,c,sticky in [('vertical','y',0,1,'ns'),('horizontal','x',1,0,'ew')]:
            bar=AutoScrollbar(listing,orient=orient,command=getattr(self.job_list,axis+'view'));bar.grid(row=r,column=c,sticky=sticky)
            self.job_list.configure(**{axis+'scrollcommand':bar.set})
        self.job_list.tag_configure('In pausa',foreground='#697386')
        self.job_list.bind('<<TreeviewSelect>>',self.choose_job)
        self.job_list.bind('<Double-1>',lambda _event:self.edit_selected())
        self.row_actions=TreeRowActions(self.job_list,on_toggle=self.toggle_job_id,on_delete=self.delete_job_id,
                                      is_paused=lambda job_id:not self.job_enabled.get(job_id,False))
        self.job_enabled={}
        self.inline_host=ttk.Frame(self.split,padding=(0,10,0,0));self.split.add(self.inline_host,weight=4)
        self.inline_hint=ttk.Label(self.inline_host,text='Seleziona una programmazione per modificarla qui.',style='Muted.TLabel')
        self.inline_hint.pack(anchor='w',pady=12)
        self.job_detail=tk.StringVar()
        self.editor=ttk.Frame(self)
        top=ttk.Frame(self.editor);top.pack(fill='x',pady=(0,12))
        self.back_button=ttk.Button(top,text='← Programmazioni',command=self.editor_back);self.back_button.pack(side='left')
        self.editor_title=tk.StringVar(value='1 · Destinatari')
        ttk.Label(top,textvariable=self.editor_title,style='Section.TLabel').pack(side='left',padx=14)
        self.continue_button=ttk.Button(top,text='Continua (0) →',command=self.next_step,style='Primary.TButton',state='disabled');self.continue_button.pack(side='right')
        self.pick_page=ttk.Frame(self.editor)
        filters=ttk.Frame(self.pick_page);filters.pack(fill='x',pady=(0,8))
        self.search=tk.StringVar();self.app_filter=tk.StringVar(value='Tutte');self.project_filter=tk.StringVar(value='Tutti')
        self.state_filter=tk.StringVar(value='Tutti');self.period_filter=tk.StringVar(value='Ultime 24 ore')
        def field(col,label,weight):
            filters.columnconfigure(col,weight=weight)
            frame=ttk.Frame(filters);frame.grid(row=0,column=col,sticky='ew',padx=(0,8 if col<4 else 0))
            ttk.Label(frame,text=label,style='Muted.TLabel').pack(anchor='w',pady=(0,5));return frame
        ttk.Entry(field(0,'Cerca',3),textvariable=self.search,width=12).pack(fill='x')
        ttk.Combobox(field(1,'Applicazione',1),textvariable=self.app_filter,values=('Tutte','Codex','Claude'),state='readonly',width=7).pack(fill='x')
        ttk.Combobox(field(2,'Periodo',1),textvariable=self.period_filter,values=('Ultime 24 ore','Ultimi 7 giorni','Ultimi 30 giorni','Tutte'),state='readonly',width=12).pack(fill='x')
        self.project_options=ttk.Combobox(field(3,'Progetto',2),textvariable=self.project_filter,values=('Tutti',),state='readonly',width=11);self.project_options.pack(fill='x')
        self.state_options=ttk.Combobox(field(4,'Stato',2),textvariable=self.state_filter,values=('Tutti',),state='readonly',width=11);self.state_options.pack(fill='x')
        for variable in (self.search,self.app_filter,self.project_filter,self.state_filter,self.period_filter):variable.trace_add('write',self.filter_changed)
        catalog_label=ttk.Label(self.pick_page,textvariable=self.catalog_status,style='Muted.TLabel',wraplength=720);catalog_label.pack(fill='x',pady=(6,8))
        catalog_label.bind('<Configure>',lambda e:catalog_label.configure(wraplength=max(120,e.width-4)),add='+')
        chat_table=ttk.Frame(self.pick_page);chat_table.pack(fill='both',expand=True);chat_table.columnconfigure(0,weight=1);chat_table.rowconfigure(0,weight=1)
        self.chat_list=ttk.Treeview(chat_table,columns=('app','title','session','group','state','activity'),show='headings',height=6,selectmode='extended')
        for col,label,width in [('app','App',60),('title','Chat',230),('session','ID sessione',100),('group','Progetto',160),('state','Stato',150),('activity','Ultima attività',110)]:
            self.chat_list.heading(col,text=label,anchor='w');self.chat_list.column(col,width=width,minwidth=60,anchor='w')
        self.chat_list.grid(row=0,column=0,sticky='nsew')
        for orient,axis,r,c,sticky in [('vertical','y',0,1,'ns'),('horizontal','x',1,0,'ew')]:
            bar=AutoScrollbar(chat_table,orient=orient,command=getattr(self.chat_list,axis+'view'));bar.grid(row=r,column=c,sticky=sticky);self.chat_list.configure(**{axis+'scrollcommand':bar.set})
        self.empty_chats=ttk.Label(chat_table,text='Caricamento delle chat…',style='Muted.TLabel',justify='center',wraplength=430)
        self.chat_list.bind('<<TreeviewSelect>>',self.choose_chat);self.chat_list.bind('<Button-1>',self.click_chat)
        self.chat_list.bind('<space>',lambda _:self.toggle_chat_row(self.chat_list.focus()))
        selected_bar=ttk.Frame(self.pick_page);selected_bar.pack(fill='x',pady=(10,0))
        self.target_label=tk.StringVar(value='Nessuna chat selezionata')
        ttk.Label(selected_bar,textvariable=self.target_label,wraplength=380).pack(side='left',fill='x',expand=True)
        self.clear_targets_button=ttk.Button(selected_bar,text='Svuota selezione',command=self.clear_targets,state='disabled');self.clear_targets_button.pack(side='right')
        self.refresh_button=ttk.Button(selected_bar,text='Aggiorna elenco',command=self.refresh);self.refresh_button.pack(side='right',padx=8)
        ttk.Label(self.pick_page,text='Scegli una o più chat, poi Continua. Clicca una riga per aggiungerla o toglierla; la selezione resta conservata quando cambi filtro.',style='Muted.TLabel',wraplength=720).pack(fill='x',pady=(12,0))
        # The common parent lets the same editor live below the list or in New.
        self.write_page=ttk.Frame(self)
        self.composer_title=tk.StringVar(value='Messaggio')
        self.composer_label=ttk.Label(self.write_page,textvariable=self.composer_title,style='Section.TLabel');self.composer_label.pack(fill='x',pady=(0,6))
        composer=ttk.Frame(self.write_page);composer.pack(fill='both',expand=True)
        self.message=tk.Text(composer,bg='#f7f9fc',fg='#17243b',height=4,width=1,wrap='word',undo=True,font=('Segoe UI',11),relief='flat',padx=12,pady=10,highlightthickness=1,highlightbackground='#dce4f0')
        self.message.insert('1.0',DEFAULT_MESSAGE)
        scroll=AutoScrollbar(composer,orient='vertical',command=self.message.yview);self.message.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right',fill='y');self.message.pack(side='left',fill='both',expand=True)
        self.minutes=tk.StringVar(value='15');self.repeat=tk.BooleanVar(value=True);self.send_when_active=tk.BooleanVar(value=False);self.send_when_present=tk.BooleanVar(value=False)
        options=ttk.Frame(self.write_page);options.pack(fill='x',pady=(12,2));self.option_columns=options
        for col in range(4):options.columnconfigure(col,weight=1,uniform='options')
        for col,heading in enumerate(('Frequenza','Ripetizione','Se la chat lavora','Se usi il PC')):
            ttk.Label(options,text=heading,style='Muted.TLabel').grid(row=0,column=col,sticky='w',pady=(0,6))
        frequency=ttk.Frame(options);frequency.grid(row=1,column=0,sticky='w',padx=(0,12))
        ttk.Combobox(frequency,textvariable=self.minutes,values=(5,10,15,30,60),width=5).pack(side='left')
        ttk.Label(frequency,text='minuti',style='Muted.TLabel').pack(side='left',padx=(6,0))
        for col,(label,var,hint) in enumerate([
            ('Ripeti',self.repeat,'Acceso: ripete il messaggio all’intervallo scelto. Spento: invia una sola volta.'),
            ('Invia anche',self.send_when_active,'Acceso: consente l’invio o l’accodamento mentre la chat lavora. Spento: aspetta che sia libera.'),
            ('Invia anche',self.send_when_present,'Acceso: consente l’invio nativo senza prendere il focus. Spento: attende l’inattività. La digitazione e le bozze restano protette.')],start=1):
            switch=Switch(options,text=label,variable=var,help_text=hint)
            switch.label.configure(wraplength=115)
            switch.grid(row=1,column=col,sticky='ew',padx=(0,10 if col<3 else 0),pady=2)
            def update_label(*_,control=switch,value=var,repeated=col==1):
                control.label.configure(text=('Ripeti' if value.get() else 'Una volta') if repeated else ('Invia anche' if value.get() else 'Attendi'))
            var.trace_add('write',update_label);update_label()
        bar=ttk.Frame(self.write_page);bar.pack(fill='x',pady=(8,0));self.editor_actions=bar
        self.commit_button=ttk.Button(bar,text='Salva modifiche',command=self.activate,style='Primary.TButton');self.commit_button.pack(side='right')
        self.save_text_button=ttk.Button(bar,text='Salva nei messaggi',command=lambda:self.library_callback());self.save_text_button.pack(side='right',padx=(0,8))
        ttk.Button(bar,text='Usa messaggio salvato',command=self.choose_saved_message).pack(side='right',padx=(0,8))
        self.history_button=ttk.Button(bar,text='Registro invii',command=self.show_history);self.history_button.pack(side='right',padx=(0,8))
        self.recovery_button=ttk.Button(heading,text='Gestisci invii incerti…',command=self.recover_legacy)
        self.status_label=ttk.Label(self.write_page,textvariable=self.status,foreground='#a44324',wraplength=720,anchor='e',justify='right')
        self.status.trace_add('write',self.show_status)
        self.show_status()
        self.editor_baseline=self.editor_signature()
        self.render_jobs()
        self.after(500,self.poll)

    def choose_saved_message(self):
        from message_library import MessageLibraryChooser
        MessageLibraryChooser(self,self.use_saved_message)

    def recover_legacy(self):
        """Only an explicit user click can authorize future legacy slots."""
        if self.recovery_running or not self.may_leave():return
        job=next((j for j in load_jobs() if j['id']==self.selected_job),None)
        if job is None:return
        self.recovery_running=True;self.recovery_button.configure(state='disabled')
        def inspect():
            from delivery_recovery import list_legacy_pending
            try:
                store=TimerStore(ROOT/'logs/messages.sqlite3')
                try:pending=list_legacy_pending(store,job)
                finally:store.db.close()
                self.recovery_results.put(('confirm',job,pending))
            except Exception as exc:self.recovery_results.put(('error',job,str(exc)))
        threading.Thread(target=inspect,daemon=True).start()

    def poll_recovery(self):
        try:kind,job,value=self.recovery_results.get_nowait()
        except queue.Empty:return
        self.recovery_running=False;self.recovery_button.configure(state='normal')
        if kind=='error':self.status.set('Nessuna modifica: '+value);return
        if kind=='done':
            self.render_jobs()
            self.status.set('Ripartenza dai soli invii futuri salvata. Il vecchio invio resta senza conferma.'
                if value['action']=='future_only' else 'Programmazione terminata senza confermare il vecchio invio.')
            return
        if not value:
            self.status.set('Non ci sono vecchi invii da sbloccare. La verifica delle ricevute native è automatica.')
            return
        if job['id']!=self.selected_job:
            self.status.set('Seleziona di nuovo la programmazione da sbloccare.');return
        repeat=job.get('repeat',True)
        decision=('Ripartire dalla prossima scadenza futura, saltando gli intervalli già trascorsi?'
                  if repeat else 'Terminare questa programmazione singola senza ripetere il vecchio invio?')
        if not messagebox.askyesno('Vecchio invio senza conferma',
                f'Chat: {job["title"]}\n\nUn vecchio tentativo non ha una ricevuta verificata. '
                'Non è possibile stabilire con certezza se sia stato ricevuto.\n\n'+decision+
                '\n\nIl vecchio tentativo non sarà ripetuto né segnato come ricevuto. '
                'Bozze, testo, pause e ultima consegna confermata resteranno invariati.',
                parent=self,default='no'):return
        self.recovery_running=True;self.recovery_button.configure(state='disabled')
        def apply():
            from delivery_recovery import apply_resolution
            try:
                report=apply_resolution(ROOT,job['id'],job['version'],[row['token'] for row in value],time.time())
                self.recovery_results.put(('done',job,report))
            except Exception as exc:self.recovery_results.put(('error',job,str(exc)))
        threading.Thread(target=apply,daemon=True).start()

    def show_status(self,*_):
        if self.status.get():self.status_label.pack(fill='x',pady=(4,0),after=self.editor_actions)
        else:self.status_label.pack_forget()

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
        if self.selected_job:self.show_inline()
        else:
            if not self.editor_visible:self.show_editor()
            if self.targets:self.next_step()
        self.status.set('')
        return True

    def show_inline(self):
        self.editor.pack_forget()
        self.pick_page.pack_forget()
        self.list_view.pack(fill='both',expand=True)
        self.inline_hint.pack_forget()
        self.write_page.pack_forget()
        self.write_page.pack(in_=self.inline_host,fill='both',expand=True)
        self.editor_visible=False;self.editor_step=2
        self.commit_button.configure(text='Salva modifiche')
        self.history_button.pack(side='right',padx=(0,8))
        self.composer_title.set('Messaggio · '+(self.target or {}).get('title',''))

    def show_list(self):
        self.editor.pack_forget()
        self.list_view.pack(fill="both", expand=True)
        self.editor_visible = False
        if self.selected_job:self.show_inline()
        else:
            self.write_page.pack_forget()
            self.inline_hint.pack(anchor='w',pady=12)

    def show_pick(self):
        self.write_page.pack_forget()
        self.pick_page.pack(fill="both", expand=True)
        self.editor_step = 1
        self.editor_title.set("1 · Scegli le chat")
        self.back_button.configure(text="← Messaggi")
        self.continue_button.pack(side='right')
        self.continue_button.configure(text=f'Continua ({len(self.targets)}) →')
        self.status.set('')

    def editor_back(self):
        if self.selected_job:self.show_inline()
        elif self.editor_step == 2:self.show_pick()
        elif self.may_leave():self.show_list()

    def next_step(self):
        if not self.targets:
            self.status.set("Scegli almeno una chat per continuare.")
            return
        if self.selected_job:
            self.show_inline();return
        self.pick_page.pack_forget()
        self.write_page.pack_forget()
        self.write_page.pack(in_=self.editor,fill="both", expand=True)
        self.editor_step = 2
        self.editor_title.set("2 · Scrivi e programma")
        self.back_button.configure(text="← Destinatari")
        self.continue_button.pack_forget()
        self.commit_button.configure(text='Attiva programmazione')
        self.history_button.pack_forget()
        self.composer_title.set('Messaggio')
        self.status.set('')

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
        if self.is_dirty():
            if self.selected_job==job['id']:
                self.show_inline();return
            if not self.may_leave():
                if self.selected_job in self.job_list.get_children():self.job_list.selection_set(self.selected_job)
                return False
        self.load_editor(job)
        return True

    def load_editor(self, job):
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
        self.status.set('')
        self.show_inline()

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
        from chat_identity import key
        return key(row) if key(row)[1] else (row['app'],row['title'],row['group'])

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
        if self.rendering_chats or not self.editor_visible or self.editor_step != 1:
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
        if not self.may_leave():return False
        self.reset_editor()
        self.show_editor()
        return True

    def reset_editor(self):
        self.selected_job = None
        self.target = None
        self.job_list.selection_remove(self.job_list.selection())
        self.clear_targets()
        self.message.delete("1.0", "end")
        self.message.insert("1.0", DEFAULT_MESSAGE)
        self.minutes.set("15")
        self.repeat.set(True)
        self.send_when_active.set(False)
        self.send_when_present.set(False)
        self.status.set('')
        self.editor_baseline=self.editor_signature()

    def editor_signature(self):
        return (self.message.get('1.0','end-1c'),self.minutes.get(),self.repeat.get(),self.send_when_active.get(),self.send_when_present.get(),tuple(sorted(self.identity(row) for row in self.targets)))

    def is_dirty(self):
        return self.editor_baseline is not None and self.editor_signature()!=self.editor_baseline

    def discard_changes(self):
        job=next((item for item in load_jobs() if item['id']==self.selected_job),None)
        if job:self.load_editor(job)
        else:
            self.reset_editor()
            self.show_list()

    def may_leave(self):
        return resolve_unsaved(self,dirty=self.is_dirty(),save=self.activate,discard=self.discard_changes)

    def activate(self):
        try:
            identity=MessagesPanel.identity
            if not self.targets or any(row.get("ambiguous") for row in self.targets):
                raise ValueError("Scegli almeno una chat senza nomi duplicati. Se il nome è duplicato, rinomina la chat nell’app e aggiorna l’elenco.")
            jobs = load_jobs()
            if self.selected_job and not any(j['id']==self.selected_job for j in jobs):
                raise ValueError('La programmazione è stata eliminata. Il testo resta nell’editor: crea una nuova programmazione per riutilizzarlo.')
            saved = []
            preserved_schedule = False
            for target in self.targets:
                previous = next((j for j in jobs if j['id']==self.selected_job),None) if self.selected_job else None
                previous = previous or next((j for j in jobs if identity(j)==identity(target)),None)
                if previous is None and len(identity(target))==3:
                    legacy=[j for j in jobs if (j['app'],j['title'],j['group'])==(target['app'],target['title'],target['group'])]
                    if len(legacy)==1:previous=legacy[0]
                job_id = previous["id"] if previous else str(uuid.uuid4())
                # Re-selecting a sidebar row must not erase its pinned thread
                # identity, transport, or explicit send-while-active policy.
                editing_existing=bool(previous and self.selected_job==previous['id'])
                if editing_existing and target.get('version')!=previous.get('version'):
                    raise ValueError('La programmazione è cambiata mentre la modificavi. Il testo resta nell’editor; ricarica la programmazione prima di salvare.')
                job = {**(previous or {}), **target, "id": job_id, "version": str(uuid.uuid4()), "enabled": bool(previous.get('enabled')) if editing_existing else True, "activated_at": time.time(), "interval_seconds": float(self.minutes.get().replace(",", ".")) * 60, "message": self.message.get("1.0", "end-1c"), "repeat": self.repeat.get()}
                if previous and identity(previous)==identity(target):
                    for field in ('title','group','project','thread_id','session_id'):
                        if field in previous:job[field]=previous[field]
                if job["app"] == "codex":
                    if not job.get("thread_id"):
                        raise ValueError("Aggiorna l'elenco per verificare l'identità della chat prima di programmare l'invio.")
                    job["transport"] = "codex_app"
                elif not previous and not job.get('session_id'):
                    raise ValueError("Aggiorna l'elenco per verificare l'ID della sessione Claude prima di programmare l'invio.")
                active_option = getattr(self, "send_when_active", None)
                if active_option is not None:
                    job["send_when_active"] = bool(active_option.get())
                present_option = getattr(self, 'send_when_present', None)
                if present_option is not None:
                    job['send_when_present'] = bool(present_option.get())
                if previous and identity(job)==identity(previous) and all(job.get(k) == previous.get(k) for k in ['message', 'interval_seconds', 'repeat', 'app', 'thread_id', 'session_id', 'transport']):
                    job['version'] = previous['version']
                    job['activated_at'] = previous['activated_at']
                    preserved_schedule = True
                validate_job(job)
                jobs = [j for j in jobs if j["id"] != job["id"]]
                jobs.append(job)
                saved.append(job_id)
            save_jobs(jobs)
            self.selected_job = saved[0] if len(saved) == 1 else None
            # The JSON commit is authoritative. A busy journal must not turn a
            # successful file save into a false unsaved state or an exception.
            if len(saved)==1:
                self.target=next(j for j in jobs if j['id']==saved[0])
                self.targets=[self.target]
            self.editor_baseline=self.editor_signature()
            journal_error=None
            try:
                store = TimerStore(ROOT / "logs" / "messages.sqlite3")
                try:
                    for item in jobs:
                        if item["id"] in saved:store.prepare(item, time.time())
                finally:store.db.close()
            except sqlite3.Error as exc:journal_error=str(exc)
            self.loading_job=True
            try:
                self.render_jobs()
                if len(saved)==1:self.job_list.selection_set(saved[0])
            except sqlite3.Error as exc:journal_error=str(exc)
            finally:self.loading_job=False
            self.show_list()
            text='Modifiche salvate.' if preserved_schedule else 'Programmazione salvata.'
            if journal_error:text+=' Registro non disponibile: il controllore riproverà il timer automaticamente. '+journal_error
            self.status.set(text)
            return True
        except (OSError, ValueError) as exc:
            self.status.set(str(exc))
            return False

    def choose_job(self, _=None):
        if self.rendering_jobs or self.editor_visible or self.loading_job:return
        selected=self.job_list.selection()
        if not selected:return
        if selected[0]==self.selected_job:return
        self.loading_job=True
        try:self.edit_selected()
        finally:self.loading_job=False

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
        return self.edit_selected()

    def delete_job_id(self, job_id):
        job=next((item for item in load_jobs() if item['id']==job_id),None)
        if job is None:return False
        if not messagebox.askyesno('Elimina programmazione',
                f'Eliminare la programmazione per «{job["title"]}»?\n\n'
                'Ferma gli invii futuri. Gli invii già avviati possono completarsi. '
                'Il registro e le bozze nelle chat vengono conservati.',parent=self,default='no'):
            return False
        # Do not silently dispose of an edited message, including deletion of
        # the selected job. Deleting another row leaves the editor untouched.
        if self.selected_job==job_id and not self.may_leave():return False
        try:
            jobs=load_jobs()
            save_jobs([item for item in jobs if item['id']!=job_id])
            if self.selected_job==job_id:
                self.reset_editor();self.show_list()
            self.render_jobs()
            return True
        except (OSError,ValueError) as exc:
            self.status.set(f'Eliminazione non riuscita: {exc}')
            return False

    def set_enabled(self, enabled, job_ids=None):
        selected = job_ids if job_ids is not None else self.job_list.selection()
        if not selected:
            self.status.set("Seleziona un messaggio nell'elenco")
            return
        jobs = load_jobs()
        self.job_enabled={job['id']:bool(job.get('enabled')) for job in jobs}
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

    def render_jobs(self):
        self.last_render = time.monotonic()
        jobs = load_jobs()
        self.job_enabled={job['id']:bool(job.get('enabled')) for job in jobs}
        store = TimerStore(ROOT / "logs" / "messages.sqlite3")
        states = {r["id"]: r for r in store.rows()}
        receipts={}
        for row in store.history(outcome='confirmed'):
            if row.get('at') is not None:receipts[row['job_id']]=max(row['at'],receipts.get(row['job_id'],0))
        store.db.close()
        existing = set(self.job_list.get_children())
        config = load_config()
        waiting_reason = action_block_reason(config)
        now = time.time()
        self.display_states = {}
        self.rendering_jobs=True
        for job in jobs:
            display = schedule_display(job, states.get(job["id"]), config, now, waiting_reason)
            self.display_states[job["id"]] = display
            last=clock_label(receipts[job['id']],now) if job['id'] in receipts else 'Ricevuta non disponibile'
            values = (job['app'].title(),job["title"], display["status"], display["next_precise"], last, '', '')
            if job["id"] in existing:
                if tuple(self.job_list.item(job["id"], "values")) != values:
                    self.job_list.item(job["id"], values=values, tags=(display["activation"],))
                existing.remove(job["id"])
            else:
                self.job_list.insert("", "end", iid=job["id"], values=values, tags=(display["activation"],))
        for key in existing:
            self.job_list.delete(key)
        self.rendering_jobs=False
        self.row_actions.refresh()
        # Quarantine can belong to an old version or another job of this ID.
        # Inspect on demand, without resolving or reading a Claude index on Tk.
        if self.selected_job:self.recovery_button.pack(side='right',padx=(0,8))
        else:self.recovery_button.pack_forget()
        self.choose_job()

    def poll(self):
        self.poll_recovery()
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
