"""Workspace presentation. Native automation and configuration remain separate."""
from datetime import datetime
import json
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

from app_version import VERSION
from automation_policy import ROOT, app_enabled, load_config
from message_engine import TimerStore, load_jobs
from polling_policy import interval
from startup_manager import startup_enabled
from switch_control import Switch, Tooltip
from ui_components import AutoScrollbar, photo_icon

BG = "#f3f5f9"
INK = "#17243b"
MUTED = "#56657a"
BLUE = "#2563eb"
NAV = "#111d32"


class PageNavigation:
    """Guard every route, including keyboard/programmatic notebook changes."""

    def __init__(self, notebook, pages, on_changed):
        self.notebook=notebook
        self.pages={str(page):page for page in pages}
        self.current=notebook.select()
        self.on_changed=on_changed
        self.busy=False
        notebook.bind('<<NotebookTabChanged>>',self.changed)

    def select(self, page):
        target=str(page)
        if target==self.current:
            self.notebook.select(page)
            return True
        if self.busy:return False
        self.busy=True
        try:
            source=self.pages.get(self.current)
            if source is not None and hasattr(source,'may_leave') and not source.may_leave():
                return False
            self.current=target
            self.notebook.select(page)
            self.on_changed()
            return True
        finally:self.busy=False

    def changed(self, _event=None):
        if self.busy:return
        selected=self.notebook.select()
        if selected!=self.current:
            # Show the editor while asking about its edits, not the new page.
            self.notebook.select(self.current)
            self.select(self.pages[selected])
        else:self.on_changed()

    def may_close(self):
        if self.busy:return False
        self.busy=True
        try:
            ordered=[self.current,*[key for key in self.pages if key!=self.current]]
            for key in ordered:
                page=self.pages[key]
                if hasattr(page,'is_dirty') and page.is_dirty():
                    self.current=key
                    self.notebook.select(page)
                    self.on_changed()
                    if not page.may_leave():return False
            return True
        finally:self.busy=False


def title(parent, text, subtitle=None):
    ttk.Label(parent, text=text, style="Section.TLabel").pack(anchor="w")
    if subtitle:
        ttk.Label(parent, text=subtitle, style="Muted.TLabel", wraplength=700).pack(anchor="w", pady=(5, 14))


def card(parent, label=None, **pack):
    frame = ttk.Frame(parent, padding=18, style="Card.TFrame")
    frame.pack(**({"fill": "x", "pady": (0, 16)} | pack))
    if label:
        title(frame, label)
    return frame


def table(parent, columns, height=7):
    body = ttk.Frame(parent)
    body.pack(fill="both", expand=True)
    tree = ttk.Treeview(body, columns=tuple(c[0] for c in columns), show="headings", height=height)
    for key, label, width in columns:
        tree.heading(key, text=label, anchor='w')
        tree.column(key, width=width, minwidth=55, stretch=True)
    vertical = AutoScrollbar(body, orient="vertical", command=tree.yview)
    horizontal = AutoScrollbar(body, orient="horizontal", command=tree.xview)
    tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
    body.columnconfigure(0, weight=1); body.rowconfigure(0, weight=1)
    tree.grid(row=0, column=0, sticky="nsew")
    vertical.grid(row=0, column=1, sticky="ns")
    horizontal.grid(row=1, column=0, sticky="ew")
    return tree


def reading_area(parent, height=5):
    """A consistently padded, scrollable inspector for long local records."""
    body = ttk.Frame(parent)
    body.pack(fill='both', expand=True, pady=(10, 0))
    text = tk.Text(body, height=height, wrap='word', relief='flat', font=('Segoe UI', 10),
                   bg='#f7f9fc', fg=INK, padx=14, pady=12, highlightthickness=1,
                   highlightbackground='#e2e8f0', highlightcolor=BLUE)
    scroll = AutoScrollbar(body, orient='vertical', command=text.yview)
    text.configure(yscrollcommand=scroll.set)
    scroll.pack(side='right', fill='y')
    text.pack(side='left', fill='both', expand=True)
    return text


def apply_runtime_status(app, paused):
    """One control reports the current state and offers the opposite action."""
    label = 'In pausa' if paused else 'Sentinella attiva'
    app.status.set(label)
    app.pause_automations.configure(text=label,
        style='PausedState.TButton' if paused else 'RunningState.TButton',
        image=app.pause_icons['play' if paused else 'pause'], compound='left')
    if hasattr(app, 'pause_tooltip'):
        app.pause_tooltip.text = ('Riprendi le automazioni abilitate. I singoli messaggi in pausa restano sospesi.' if paused else
                                 'Metti in pausa tutte le automazioni. La lettura delle notifiche continua.')
    window_title = 'Sentinella · In pausa' if paused else 'Sentinella · Attiva'
    if app.root.title().startswith('PROVA LOCALE'):
        window_title = 'PROVA LOCALE · ' + window_title
    if app.root.title() != window_title:
        app.root.title(window_title)


def build_operational_overview(app, overview, history_for_job):
    """The essential list owns all dashboard presentation and on-demand detail."""
    from agenda_view import AgendaView
    from operations_view import AutomationOverview
    def edit(job_id):
        if app.navigate_to(app.messages_panel):app.messages_panel.edit_job_id(job_id)
    def toggle(job_id):
        app.messages_panel.toggle_job_id(job_id)
        update_insights(app,load_config())
    def new():
        if app.navigate_to(app.messages_panel):app.messages_panel.new()
    overview.columnconfigure(0,weight=1);overview.rowconfigure(1,weight=1)
    app.dashboard_automations=AutomationOverview(overview,lambda:app.navigate_to(app.settings_panel))
    app.dashboard_automations.grid(row=0,column=0,sticky='ew',pady=(0,8))
    app.dashboard_agenda=AgendaView(overview,edit,toggle,history_for_job,new)
    app.dashboard_agenda.grid(row=1,column=0,sticky='nsew')


def history_event_label(row):
    if row['event'] == 'Da verificare':
        return 'Invio avviato' if row.get('detail', '').startswith('Invio iniziato:') else 'Ricezione non confermata'
    return row['event']


def start_cleanup(parent, button, operation, on_done):
    """Keep large journals off the UI loop; completion returns on the Tk thread."""
    if button.instate(['disabled']):return
    results=queue.Queue()
    button.configure(state='disabled',text='Pulizia…')
    def run():
        try:results.put(operation())
        except Exception as exc:results.put({'complete':False,'errors':[str(exc)]})
    def poll():
        if not parent.winfo_exists():return
        try:result=results.get_nowait()
        except queue.Empty:
            parent.after(100,poll);return
        button.configure(state='normal',text='Elimina tutto')
        on_done(result)
    threading.Thread(target=run,daemon=True).start()
    parent.after(100,poll)


class HistoryPanel(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, padding=20)
        self.target = tk.StringVar(value="Tutte le programmazioni")
        self.outcome = tk.StringVar(value="Tutti gli esiti")
        self.period = tk.StringVar(value="Tutto lo storico")
        filters = ttk.Frame(self); filters.pack(fill="x", pady=(0, 14))
        for col in range(3):filters.columnconfigure(col,weight=1)
        self.targets = ttk.Combobox(filters, textvariable=self.target, state="readonly", width=1)
        self.targets.grid(row=0,column=0,sticky='ew',padx=(0,10))
        ttk.Combobox(filters, textvariable=self.outcome, state="readonly", width=1, values=("Tutti gli esiti", "Consegne verificate", "Eventi di verifica", "Attese e preparazione")).grid(row=0,column=1,sticky='ew',padx=(0,10))
        ttk.Combobox(filters, textvariable=self.period, state="readonly", width=1, values=("Tutto lo storico", "Ultime 24 ore", "Ultimi 7 giorni")).grid(row=0,column=2,sticky='ew',padx=(0,14))
        self.delete_icon=photo_icon(self,'trash',color='white')
        self.delete_all_button=ttk.Button(filters,text='Elimina tutto',image=self.delete_icon,compound='left',style='Primary.TButton',command=self.clear_all)
        self.delete_all_button.grid(row=0,column=3,sticky='e')
        self.count = tk.StringVar()
        ttk.Label(self, textvariable=self.count, style="Muted.TLabel").pack(anchor="w", pady=(0, 10))
        self.split = ttk.Panedwindow(self, orient="vertical"); self.split.pack(fill="both", expand=True)
        listing = ttk.Frame(self.split); self.split.add(listing, weight=3)
        self.tree = table(listing, (("at", "Quando", 140), ("title", "Destinatario", 220), ("event", "Esito", 280)), 8)
        detail = ttk.Frame(self.split, padding=(0, 16)); self.split.add(detail, weight=1)
        title(detail, "Dettaglio della consegna")
        self.detail = reading_area(detail)
        self.detail.configure(state="disabled")
        self.tree.bind("<<TreeviewSelect>>", self.select)
        self.rows = {}; self.signature = None
        for var in (self.target, self.outcome, self.period):
            var.trace_add("write", lambda *_: self.refresh())
        self.refresh(); self.after(5000, self.poll)

    def clear_all(self):
        if not messagebox.askyesno('Elimina tutto il registro invii',
                'Eliminare tutti gli eventi del registro invii, anche quelli esclusi dai filtri, '
                'e i file di ricevuta esportati già confermati?\n\n'
                'Programmazioni, scadenze e bozze restano. Le prenotazioni e le prove operative '
                'necessarie a evitare duplicati vengono conservate. La pulizia è definitiva.',parent=self,default='no'):
            return False
        from history_cleanup import clear_delivery_history
        def done(result):
            self.signature=None
            self.refresh()
            self.count.set('Registro invii e file associati eliminati.' if result.get('complete') else
                           'Pulizia incompleta: '+'; '.join(result.get('errors',[])))
        start_cleanup(self,self.delete_all_button,lambda:clear_delivery_history(ROOT),done)
        return True

    def open_job(self, job_id):
        self.refresh()
        self.target.set(next((label for label, key in self.job_options.items() if key == job_id), "Tutte le programmazioni"))
        self.refresh()

    def refresh(self):
        self.job_options = {"Tutte le programmazioni": None}
        jobs = load_jobs()
        for job in jobs:
            label = job['title']
            if sum(j['title'] == label for j in jobs) > 1:
                label += ' · ' + job['app'].title() + ' · ' + job['group']
            base = label; number = 2
            while label in self.job_options:
                label = f'{base} · {number}'; number += 1
            self.job_options[label] = job['id']
        self.targets.configure(values=tuple(self.job_options))
        days = {"Ultime 24 ore": 1, "Ultimi 7 giorni": 7}.get(self.period.get())
        job_id = self.job_options.get(self.target.get())
        outcome = {'Consegne verificate': 'confirmed', 'Eventi di verifica': 'attention', 'Attese e preparazione': 'waiting'}.get(self.outcome.get())
        store = TimerStore(ROOT / "logs/messages.sqlite3")
        try:
            rows = store.history(job_id, since=time.time()-days*86400 if days else None, outcome=outcome)
        finally:
            store.db.close()
        signature = json.dumps(rows, sort_keys=True)
        if signature == self.signature:
            return
        self.signature = signature
        self.count.set(f"{len(rows)} eventi · Ultimi 200 risultati dei filtri selezionati. Invio e ricezione sono esiti distinti." if rows else "Nessun evento con questi filtri. Gli invii futuri compariranno qui.")
        existing = set(self.tree.get_children()); self.rows = {}
        for index, row in enumerate(rows):
            key = f"event:{row['seq']}" if row.get('seq') is not None else f"receipt:{row['legacy_id']}"
            self.rows[key] = row
            values = (datetime.fromtimestamp(row['at']).strftime('%d/%m %H:%M:%S'), row['title'], history_event_label(row))
            if key in existing:
                self.tree.item(key, values=values); existing.remove(key)
            else:
                self.tree.insert('', 'end', iid=key, values=values)
            self.tree.move(key, '', index)
        for key in existing:
            self.tree.delete(key)
        self.select()

    def select(self, _=None):
        selected = self.tree.selection()
        row = self.rows.get(selected[0]) if selected else None
        text = f"{row['title']}\n{history_event_label(row)}\n\n{row['detail']}" if row else "Seleziona un evento per leggere il motivo completo e la prova disponibile."
        if text != getattr(self, 'detail_value', None):
            self.detail_value = text
            self.detail.configure(state="normal"); self.detail.delete('1.0', 'end'); self.detail.insert('1.0', text); self.detail.configure(state="disabled")

    def poll(self):
        if self.winfo_ismapped():
            try:
                self.refresh()
            except (OSError, ValueError):
                pass
        self.after(5000, self.poll)


def build_workspace(app):
    root = app.root
    from app_icon import set_window_icon
    set_window_icon(root)
    root.title("Sentinella · workspace")
    root.geometry('1280x850'); root.minsize(1040, 790); root.configure(bg=BG)
    style = app.style = ttk.Style(root); style.theme_use('clam')
    style.configure('TFrame', background='white')
    style.configure('TLabel', background='white', foreground=INK, font=('Segoe UI', 10))
    style.configure('Muted.TLabel', foreground=MUTED, font=('Segoe UI', 10))
    style.configure('Section.TLabel', font=('Segoe UI', 13, 'bold'))
    style.configure('Metric.TLabel', font=('Segoe UI', 24, 'bold'), foreground=INK)
    style.configure('CompactMetric.TLabel', font=('Segoe UI', 17, 'bold'), foreground=INK)
    style.configure('Card.TFrame', background='white', relief='solid', borderwidth=1, bordercolor='#e2e8f0')
    style.configure('Surface.TFrame', background=BG)
    style.configure('Surface.TLabel', background=BG, foreground=MUTED)
    style.configure('TButton', padding=(13, 9), font=('Segoe UI', 10), relief='flat', borderwidth=0, background='#eaf0f8', foreground=INK)
    style.map('TButton', background=[('active', '#dce6f4')])
    style.map('TButton', foreground=[('disabled', '#8793a4')])
    style.configure('Primary.TButton', background=BLUE, foreground='white', padding=(16, 10))
    style.map('Primary.TButton', background=[('active', '#1d4ed8'), ('disabled', '#e2e8f0')], foreground=[('disabled', MUTED)])
    for state, color, hover in (('RunningState', '#167347', '#115b38'), ('PausedState', '#ad550b', '#8e4309')):
        style.configure(state+'.TButton', background=color, foreground='white', font=('Segoe UI', 11, 'bold'), padding=(18,12))
        style.map(state+'.TButton', background=[('active',hover),('pressed',hover)], foreground=[('active','white')])
    style.configure('Treeview', font=('Segoe UI', 10), rowheight=36, background='white', fieldbackground='white', foreground=INK, borderwidth=0)
    style.configure('Treeview.Heading', background='#f0f4fa', foreground=MUTED, font=('Segoe UI', 10, 'bold'), padding=(2, 10), relief='flat')
    style.map('Treeview', background=[('selected', '#e5edff')], foreground=[('selected', '#173a85')])
    style.configure('TLabelframe', background='white', borderwidth=0)
    style.configure('TLabelframe.Label', background='white', foreground=INK, font=('Segoe UI', 12, 'bold'))
    style.configure('Workspace.TNotebook', background=BG, borderwidth=0, tabmargins=0, lightcolor=BG, darkcolor=BG, bordercolor=BG)
    style.layout('Workspace.TNotebook.Tab', [])
    style.configure('TEntry', padding=8, fieldbackground='#f7f9fc')
    style.configure('TCombobox', padding=7, fieldbackground='#f7f9fc')
    sidebar = tk.Frame(root, bg=NAV, width=192); sidebar.pack(side='left', fill='y'); sidebar.pack_propagate(False)
    tk.Label(sidebar, text='Sentinella', bg=NAV, fg='white', font=('Segoe UI', 18, 'bold')).pack(anchor='w', padx=20, pady=(28, 6))
    tk.Label(sidebar, text='SESSION WORKSPACE', bg=NAV, fg='#8ea2c1', font=('Segoe UI', 8, 'bold')).pack(anchor='w', padx=22, pady=(0, 34))
    main = ttk.Frame(root, style='Surface.TFrame', padding=(24, 22)); main.pack(side='left', fill='both', expand=True)
    header = ttk.Frame(main, style='Surface.TFrame'); header.pack(fill='x', pady=(0, 18))
    heading = ttk.Frame(header, style='Surface.TFrame'); heading.pack(side='left')
    app.page_title = tk.StringVar(); app.page_subtitle = tk.StringVar()
    tk.Label(heading, textvariable=app.page_title, bg=BG, fg=INK, font=('Segoe UI', 23, 'bold')).pack(anchor='w')
    ttk.Label(heading, textvariable=app.page_subtitle, style='Surface.TLabel',wraplength=560).pack(anchor='w', pady=(5, 0))
    actions = ttk.Frame(header, style='Surface.TFrame'); actions.pack(side='right')
    app.status = tk.StringVar()
    app.pause_automations = ttk.Button(actions, command=app.toggle_automations, style='Primary.TButton', padding=(18,12))
    app.pause_automations.pack(anchor='e')
    app.pause_tooltip = Tooltip(app.pause_automations, '')
    app.pause_icons = {name:photo_icon(root,name,size=20,color='white') for name in ('play','pause')}
    apply_runtime_status(app, load_config().get('paused', False))
    app.notebook = ttk.Notebook(main, style='Workspace.TNotebook'); app.notebook.pack(fill='both', expand=True)
    overview = ttk.Frame(app.notebook, style='Surface.TFrame')
    from messages_panel import MessagesPanel
    from settings_panel import SettingsPanel
    app.messages_panel = MessagesPanel(app.notebook)
    from message_library import MessageLibraryPanel
    app.library_panel = MessageLibraryPanel(app.notebook)
    app.history_panel = HistoryPanel(app.notebook)
    notifications = ttk.Frame(app.notebook, padding=20)
    settings = app.settings_panel = SettingsPanel(app.notebook, app.refresh_dashboard, lambda: os.startfile(str(ROOT / 'logs')), app.restart_reader)
    pages = [(overview, 'Panoramica', 'Controlli, automazioni e prossimi invii.'),
             (app.messages_panel, 'Programmazioni', 'Destinatari, frequenze e comportamento degli invii'),
             (app.library_panel, 'Messaggi salvati', 'Organizza i messaggi in raccolte e riutilizzali nelle programmazioni.'),
             (app.history_panel, 'Registro invii', 'Cronologia, motivi dei rinvii e prove di consegna'),
             (notifications, 'Notifiche', 'Eventi osservati nelle applicazioni, separati dalle ricevute'),
             (settings, 'Impostazioni', 'Comportamento, tempi, cartelle autorizzate e diagnostica')]
    buttons = []
    for index, (page, label, _) in enumerate(pages):
        app.notebook.add(page, text=label)
        button = tk.Button(sidebar, text=label, anchor='w', bg=NAV, fg='#bac8dc', activebackground='#253957', activeforeground='white', relief='flat', bd=0, padx=20, pady=14, font=('Segoe UI', 11), cursor='hand2', takefocus=True, command=lambda p=page: app.navigate_to(p))
        button.pack(fill='x', padx=10, pady=3); buttons.append(button)
    def navigate(_=None):
        index = app.notebook.index(app.notebook.select())
        app.page_title.set(pages[index][1]); app.page_subtitle.set(pages[index][2])
        for i, button in enumerate(buttons):
            button.configure(bg='#253957' if i==index else NAV, fg='white' if i==index else '#bac8dc')
        if pages[index][0] is app.history_panel:
            app.history_panel.refresh()
    app.navigation=PageNavigation(app.notebook,[row[0] for row in pages],navigate)
    app.navigate_to=app.navigation.select
    app.may_close=app.navigation.may_close
    def history_for_job(job_id=None):
        if app.navigate_to(app.history_panel):app.history_panel.open_job(job_id)
    app.messages_panel.history_callback = history_for_job
    def save_editor_text():
        from message_library import save_text_as_message
        text = app.messages_panel.message.get('1.0', 'end-1c')
        def saved(_item):
            app.library_panel.refresh(guard=False)
            app.messages_panel.status.set('Messaggio salvato nella raccolta. Le modifiche alla programmazione restano da salvare.')
        try:save_text_as_message(app.messages_panel,text,on_saved=saved)
        except (OSError,ValueError) as exc:app.messages_panel.status.set(str(exc))
    app.messages_panel.library_callback = save_editor_text
    bottom = tk.Frame(sidebar, bg=NAV); bottom.pack(side='bottom', fill='x', padx=22, pady=24)
    tk.Label(bottom, text=f'VERSIONE {VERSION}', bg=NAV, fg='#8ea2c1', font=('Segoe UI', 9, 'bold')).pack(anchor='w')
    app.pc_clock = tk.StringVar()
    tk.Label(bottom, textvariable=app.pc_clock, bg=NAV, fg='#8ea2c1', font=('Segoe UI', 9)).pack(anchor='w', pady=(8, 0))
    app.away_status = tk.StringVar(); app.schedule_status = tk.StringVar(); app.reader_status = tk.StringVar(value='Avvio del lettore…'); app.counter = tk.StringVar(value='0 eventi')
    build_operational_overview(app, overview, history_for_job)
    top=ttk.Frame(notifications);top.pack(fill='x',pady=(0,14))
    ttk.Label(top,text='Notifiche del sistema',style='Section.TLabel').pack(side='left');ttk.Label(top,textvariable=app.counter,style='Muted.TLabel').pack(side='right')
    filters=ttk.Frame(notifications);filters.pack(fill='x',pady=(0,12))
    app.notification_query=tk.StringVar();app.notification_kind=tk.StringVar(value='Tutti gli eventi')
    ttk.Label(filters,text='Cerca').pack(side='left',padx=(0,8))
    ttk.Entry(filters,textvariable=app.notification_query,width=28).pack(side='left',fill='x',expand=True,padx=(0,12))
    ttk.Combobox(filters,textvariable=app.notification_kind,state='readonly',width=24,values=('Tutti gli eventi','Domande e attese','Consensi','Altri eventi')).pack(side='left')
    app.notification_delete_icon=photo_icon(notifications,'trash',color='white')
    app.notification_delete_button=ttk.Button(filters,text='Elimina tutto',image=app.notification_delete_icon,compound='left',style='Primary.TButton')
    app.notification_delete_button.pack(side='right',padx=(14,0))
    def clear_notifications():
        if not messagebox.askyesno('Elimina tutte le notifiche',
                'Eliminare tutte le notifiche raccolte da Sentinella, anche quelle escluse dai filtri, '
                'e i contenuti registrati nei suoi file?\n\n'
                'Le notifiche di Windows, le cartelle autorizzate, le programmazioni e i registri degli invii restano invariati. '
                'La pulizia è definitiva.',parent=notifications,default='no'):
            return False
        from history_cleanup import clear_notifications as clear_notification_files
        removed=set(app.rows)
        def done(result):
            if result.get('complete'):
                for key in removed:
                    if key in app.rows:
                        app.tree.delete(key)
                        del app.rows[key]
                app.notifications_cleared_at=result.get('cleared_at',0)
                app.counter.set(f'Pulizia completata · {len(app.rows)} nuove notifiche' if app.rows else 'Notifiche e contenuti registrati eliminati.')
                app.show_details('Seleziona una delle nuove notifiche.' if app.rows else 'Nessuna notifica. Le nuove notifiche compariranno qui.')
            else:app.counter.set('Pulizia incompleta: '+'; '.join(result.get('errors',[])))
        start_cleanup(notifications,app.notification_delete_button,lambda:clear_notification_files(ROOT),done)
        return True
    app.clear_notifications=clear_notifications
    app.notification_delete_button.configure(command=clear_notifications)
    split=ttk.Panedwindow(notifications,orient='vertical');split.pack(fill='both',expand=True)
    listing=ttk.Frame(split);split.add(listing,weight=3)
    app.tree=table(listing,(('time','Quando',140),('app','Origine',90),('kind','Evento osservato',180),('title','Chat',320)),8)
    app.tree.tag_configure('approval',background='#fff6df');app.tree.tag_configure('test',foreground=MUTED);app.tree.bind('<<TreeviewSelect>>',app.select)
    detail=ttk.Frame(split,padding=(0,16));split.add(detail,weight=1);title(detail,'Contenuto della notifica')
    app.details=reading_area(detail)
    app.show_details('Seleziona una notifica. Un evento osservato non è una conferma di consegna del messaggio.')
    def filter_notifications(*_):
        from notification_core import classify
        query=app.notification_query.get().casefold();kind=app.notification_kind.get()
        for key,notification in app.rows.items():
            classification=classify(notification.title,notification.body).casefold()
            matches=(not query or query in (notification.title+' '+notification.body+' '+notification.app).casefold())
            matches=matches and (kind=='Tutti gli eventi' or kind=='Domande e attese' and 'attenzione' in classification or kind=='Consensi' and 'approv' in classification or kind=='Altri eventi' and 'attenzione' not in classification and 'approv' not in classification)
            if matches:app.tree.move(key,'',0)
            else:app.tree.detach(key)
    app.filter_notifications=filter_notifications
    for variable in (app.notification_query,app.notification_kind):variable.trace_add('write',filter_notifications)
    navigate()


def update_insights(app, config):
    if not hasattr(app, 'dashboard_agenda'): return
    from dashboard_model import snapshot
    jobs=load_jobs();store=TimerStore(ROOT/'logs/messages.sqlite3')
    try:
        states={row['id']:row for row in store.rows()};history=store.history()
    finally:
        store.db.close()
    health={}
    for key,filename in (('gui','gui-heartbeat.json'),('reader','health.json'),('supervisor','supervisor-health.json'),('recovery','file-recovery-status.json')):
        try:health[key]=json.loads((ROOT/'logs'/filename).read_text(encoding='utf-8'))
        except (OSError,ValueError):health[key]={}
    health['reader']['requested']=getattr(app,'monitoring',True)
    model=snapshot(config,jobs,states,history,health,time.time())
    app.dashboard_automations.render(model['automations'])
    app.dashboard_agenda.render(model)


def build_settings(panel, parent, on_saved, open_logs, restart_reader):
    ttk.Frame.__init__(panel,parent,padding=0)
    panel.on_saved=on_saved
    panel.canvas=tk.Canvas(panel,highlightthickness=0,bg=BG)
    scrollbar=AutoScrollbar(panel,orient='vertical',command=panel.canvas.yview)
    panel.canvas.configure(yscrollcommand=scrollbar.set);scrollbar.pack(side='right',fill='y');panel.canvas.pack(side='left',fill='both',expand=True)
    content=ttk.Frame(panel.canvas,style='Surface.TFrame',padding=(0,0,12,0))
    item=panel.canvas.create_window((0,0),window=content,anchor='nw')
    content.bind('<Configure>',lambda _:panel.canvas.configure(scrollregion=panel.canvas.bbox('all')))
    panel.canvas.bind('<Configure>',lambda e:panel.canvas.itemconfigure(item,width=e.width))
    def scroll(event):
        if str(event.widget).startswith(str(panel)) and event.widget.winfo_class() not in {'TCombobox','TEntry','Entry','Listbox'}:
            direction=-1 if event.delta>0 else 1
            panel.canvas.yview_scroll(direction*min(9,max(3,abs(event.delta)//120*3)),'units')
    panel.bind_all('<MouseWheel>',scroll,add='+')
    cfg=load_config()
    panel.notifications=tk.StringVar(value=f"{interval(cfg,'notifications'):g}")
    panel.chats=tk.StringVar(value=f"{interval(cfg,'chats')/60:g}")
    panel.idle=tk.StringVar(value=f"{float(cfg.get('idle_seconds_before_actions',30))/60:g}")
    panel.away=tk.BooleanVar(value=cfg.get('away_mode',False))
    panel.feedback=tk.StringVar()
    panel.diagnostic_feedback=tk.StringVar(value='Scegli un controllo: il risultato apparirà qui.')
    panel.reader_restart=restart_reader
    timing=card(content,'Quando intervenire')
    ttk.Label(timing,text='Tempi dei controlli e rilevamento dell’inattività.',style='Muted.TLabel').pack(anchor='w',pady=(6,14))
    fields=ttk.Frame(timing);fields.pack(fill='x')
    for row,(label,var,values,unit) in enumerate((('Lettura notifiche',panel.notifications,(15,30,60,120,300),'secondi'),('Controllo delle chat',panel.chats,(5,10,15,30,60),'minuti'),('Attiva dopo inattività',panel.idle,(0,0.5,1,2,5,10,15,30),'minuti senza input'))):
        ttk.Label(fields,text=label,width=25).grid(row=row,column=0,sticky='w',pady=6)
        ttk.Combobox(fields,textvariable=var,values=values,width=9).grid(row=row,column=1,sticky='w',padx=12,pady=6)
        ttk.Label(fields,text=unit,style='Muted.TLabel').grid(row=row,column=2,sticky='w')
    ttk.Label(timing,text='Ogni input di mouse o tastiera fa ripartire l’attesa. 0 disattiva la soglia. La pausa generale resta prioritaria.',style='Muted.TLabel',wraplength=650).pack(anchor='w',pady=(12,0))
    ttk.Button(timing,text='Salva tempi dei controlli',command=panel.save,style='Primary.TButton').pack(anchor='w',pady=(14,0))
    ttk.Label(timing,textvariable=panel.feedback,style='Muted.TLabel',wraplength=650).pack(anchor='w',pady=(10,0))
    behavior=card(content,'Azioni automatiche')
    panel.questions=tk.BooleanVar(value=cfg.get('shared_controls',{}).get('questions',any(app_enabled(cfg,a,'questions') for a in cfg['apps'])))
    panel.approvals=tk.BooleanVar(value=cfg.get('enabled',False) and cfg.get('shared_controls',{}).get('approvals',any(app_enabled(cfg,a,'approvals') for a in cfg['apps'])))
    panel.recovery=tk.BooleanVar(value=cfg.get('automatic_file_recovery',False))
    panel.commands=tk.BooleanVar(value=cfg.get('automatic_command_approval',False))
    for label,var,callback in (('Rileva domande nelle chat',panel.questions,panel.save_apps),('Risposta automatica alle domande',panel.away,panel.save_away),('Consenti operazioni sui file delegati',panel.approvals,panel.save_apps),('Recupera scritture bloccate',panel.recovery,panel.save_apps)):
        hints={'Rileva domande nelle chat':'Rileva le richieste di risposta. Da solo non invia risposte: serve anche Risposta automatica alle domande attiva.', 'Risposta automatica alle domande':'Spento: non risponde automaticamente alle domande, neppure durante la tua assenza. Acceso: invia il messaggio di assenza solo dopo l’inattività configurata e con rilevamento domande attivo. Messaggi programmati e consensi file restano indipendenti.', 'Consenti operazioni sui file delegati':'Gestisce solo consensi riconoscibili per i file nelle cartelle autorizzate. Non risponde alle domande e non autorizza eliminazioni, shell, rete o credenziali.', 'Recupera scritture bloccate':'Un solo tentativo di recupero per una scrittura già autorizzata e fallita. Richiede consensi file attivi e rispetta cartelle, pausa e bozze.'}
        Switch(behavior,text=label,variable=var,command=callback,help_text=hints[label]).pack(anchor='w',pady=8)
    Switch(behavior,text='Approva automaticamente i comandi',variable=panel.commands,command=panel.save_commands,help_text='Spento per default. Acceso: preme Consenti una volta sulle richieste di esecuzione riconosciute delle applicazioni rilevate. Può autorizzare anche comandi distruttivi, rete e operazioni fuori dalle cartelle delegate. Non concede permessi permanenti e rispetta pausa e inattività. Non modifica i consensi di Windows.').pack(anchor='w',pady=(12,4))
    ttk.Label(behavior,text='Può autorizzare cancellazioni e operazioni irreversibili, anche fuori dalle cartelle autorizzate.',style='Muted.TLabel',wraplength=650).pack(anchor='w',pady=(0,8))
    ttk.Label(behavior,text='Si applicano a tutte le applicazioni rilevate. Il recupero usa gli stessi file e richiede i consensi file attivi.',style='Muted.TLabel',wraplength=650).pack(anchor='w',pady=(6,0))
    ttk.Label(behavior,text='Interruttori salvati subito, senza cambiare la pausa generale.',style='Muted.TLabel',wraplength=650).pack(anchor='w',pady=(8,0))
    roots=card(content,'Cartelle autorizzate')
    from approvals_panel import ApprovalsPanel
    ApprovalsPanel(roots,enabled_variable=panel.approvals).pack(fill='x',pady=(8,0))
    startup=card(content,'Avvio e diagnostica')
    panel.startup=tk.BooleanVar(value=startup_enabled())
    Switch(startup,text='Avvia con Windows',variable=panel.startup,command=panel.toggle_startup,help_text='Acceso: avvia Sentinella quando accedi a Windows. Spento: aprila manualmente. Il launcher recupera i crash; una chiusura volontaria resta rispettata.').pack(anchor='w',pady=(12,8))
    tools = ttk.Frame(startup); tools.pack(fill='x',pady=(4,0))
    panel.diagnostic_buttons=[]
    for column,(text,callback) in enumerate((('Rileva applicazioni',panel.detect_apps),('Riavvia lettore',panel.restart_notifications),('Apri registri tecnici',open_logs))):
        tools.columnconfigure(column,weight=1,uniform='diagnostic-actions')
        button=ttk.Button(tools,text=text,command=callback)
        button.grid(row=0,column=column,sticky='ew',padx=(0,8 if column<2 else 0))
        if column<2:panel.diagnostic_buttons.append(button)
    ttk.Label(startup,textvariable=panel.diagnostic_feedback,style='Muted.TLabel',wraplength=650).pack(anchor='w',fill='x',pady=(12,0))
