"""Workspace presentation. Native automation and configuration remain separate."""
from datetime import datetime
import json
import os
import time
import tkinter as tk
from tkinter import ttk

from app_version import VERSION
from automation_policy import ROOT, app_enabled, load_config
from message_engine import TimerStore, load_jobs
from polling_policy import interval
from startup_manager import startup_enabled
from switch_control import Switch, Tooltip
from ui_components import AutoScrollbar

BG = "#f3f5f9"
INK = "#17243b"
MUTED = "#56657a"
BLUE = "#2563eb"
NAV = "#111d32"


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
    def edit(job_id):
        app.messages_panel.edit_job_id(job_id)
        app.notebook.select(app.messages_panel)
    def toggle(job_id):
        app.messages_panel.toggle_job_id(job_id)
        update_insights(app,load_config())
    def new():
        app.messages_panel.new()
        app.notebook.select(app.messages_panel)
    app.dashboard_agenda=AgendaView(overview,edit,toggle,history_for_job,new)
    app.dashboard_agenda.pack(fill='both',expand=True)


def history_event_label(row):
    if row['event'] == 'Da verificare':
        return 'Invio avviato' if row.get('detail', '').startswith('Invio iniziato:') else 'Ricezione non confermata'
    return row['event']


class HistoryPanel(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, padding=20)
        self.target = tk.StringVar(value="Tutte le programmazioni")
        self.outcome = tk.StringVar(value="Tutti gli esiti")
        self.period = tk.StringVar(value="Tutto lo storico")
        filters = ttk.Frame(self); filters.pack(fill="x", pady=(0, 14))
        self.targets = ttk.Combobox(filters, textvariable=self.target, state="readonly", width=27)
        self.targets.pack(side="left", padx=(0, 10))
        ttk.Combobox(filters, textvariable=self.outcome, state="readonly", width=24, values=("Tutti gli esiti", "Consegne verificate", "Eventi di verifica", "Attese e preparazione")).pack(side="left", padx=(0, 10))
        ttk.Combobox(filters, textvariable=self.period, state="readonly", width=20, values=("Tutto lo storico", "Ultime 24 ore", "Ultimi 7 giorni")).pack(side="left")
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
    style.configure('Treeview.Heading', background='#f0f4fa', foreground=MUTED, font=('Segoe UI', 10, 'bold'), padding=(8, 10), relief='flat')
    style.map('Treeview', background=[('selected', '#e5edff')], foreground=[('selected', '#173a85')])
    style.configure('TLabelframe', background='white', borderwidth=0)
    style.configure('TLabelframe.Label', background='white', foreground=INK, font=('Segoe UI', 12, 'bold'))
    style.configure('Workspace.TNotebook', background=BG, borderwidth=0, tabmargins=0, lightcolor=BG, darkcolor=BG, bordercolor=BG)
    style.layout('Workspace.TNotebook.Tab', [])
    style.configure('TEntry', padding=8, fieldbackground='#f7f9fc')
    style.configure('TCombobox', padding=7, fieldbackground='#f7f9fc')
    sidebar = tk.Frame(root, bg=NAV, width=192); sidebar.pack(side='left', fill='y'); sidebar.pack_propagate(False)
    tk.Label(sidebar, text='S  Sentinella', bg=NAV, fg='white', font=('Segoe UI', 18, 'bold')).pack(anchor='w', padx=20, pady=(28, 6))
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
    app.pause_icons = {}
    for name in ('play', 'pause'):
        icon = tk.PhotoImage(master=root, width=25, height=20)
        if name == 'pause':
            icon.put('white', to=(2,3,7,17)); icon.put('white', to=(10,3,15,17))
        else:
            for y in range(3,18):
                icon.put('white', to=(3,y,4+max(0,7-abs(10-y)),y+1))
        app.pause_icons[name] = icon
    apply_runtime_status(app, load_config().get('paused', False))
    app.notebook = ttk.Notebook(main, style='Workspace.TNotebook'); app.notebook.pack(fill='both', expand=True)
    overview = ttk.Frame(app.notebook, style='Surface.TFrame')
    from messages_panel import MessagesPanel
    from settings_panel import SettingsPanel
    app.messages_panel = MessagesPanel(app.notebook)
    from message_library import MessageLibraryPanel
    def use_library_text(text):
        if app.messages_panel.use_saved_message(text):
            app.notebook.select(app.messages_panel)
            return True
        return False
    app.library_panel = MessageLibraryPanel(app.notebook, use_library_text)
    app.history_panel = HistoryPanel(app.notebook)
    notifications = ttk.Frame(app.notebook, padding=20)
    settings = SettingsPanel(app.notebook, app.refresh_dashboard, lambda: os.startfile(str(ROOT / 'logs')), app.restart_reader)
    pages = [(overview, 'Panoramica', 'Le tue programmazioni, a colpo d’occhio.'),
             (app.messages_panel, 'Programmazioni', 'Destinatari, frequenze e comportamento degli invii'),
             (app.library_panel, 'Messaggi salvati', 'Testi riutilizzabili: organizza in raccolte, salva e usa nell’editor. Salvare un modello non programma né invia messaggi.'),
             (app.history_panel, 'Registro invii', 'Cronologia, motivi dei rinvii e prove di consegna'),
             (notifications, 'Notifiche', 'Eventi osservati nelle applicazioni, separati dalle ricevute'),
             (settings, 'Impostazioni', 'Comportamento, tempi, cartelle autorizzate e diagnostica')]
    buttons = []
    for index, (page, label, _) in enumerate(pages):
        app.notebook.add(page, text=label)
        button = tk.Button(sidebar, text=label, anchor='w', bg=NAV, fg='#bac8dc', activebackground='#253957', activeforeground='white', relief='flat', bd=0, padx=20, pady=14, font=('Segoe UI', 11), cursor='hand2', takefocus=True, command=lambda p=page: app.notebook.select(p))
        button.pack(fill='x', padx=10, pady=3); buttons.append(button)
    def navigate(_=None):
        index = app.notebook.index(app.notebook.select())
        app.page_title.set(pages[index][1]); app.page_subtitle.set(pages[index][2])
        for i, button in enumerate(buttons):
            button.configure(bg='#253957' if i==index else NAV, fg='white' if i==index else '#bac8dc')
        if pages[index][0] is app.history_panel:
            app.history_panel.refresh()
    app.notebook.bind('<<NotebookTabChanged>>', navigate)
    def history_for_job(job_id=None):
        app.history_panel.open_job(job_id)
        app.notebook.select(app.history_panel)
    app.messages_panel.history_callback = history_for_job
    def save_editor_text():
        text = app.messages_panel.message.get('1.0', 'end-1c')
        if app.library_panel.new_from_text(text):
            app.notebook.select(app.library_panel)
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


def bind_job_actions(tree,panel,show_editor,refresh=None):
    """The first column toggles; double-clicking a data cell opens its editor."""
    def click(event):
        if tree.identify_region(event.x,event.y)!='cell' or tree.identify_column(event.x)!='#1': return
        job_id=tree.identify_row(event.y)
        if job_id:
            tree.selection_set(job_id); panel.toggle_job_id(job_id)
            if refresh: refresh()
        return 'break'
    def double(event):
        if tree.identify_column(event.x)=='#1': return 'break'
        job_id=tree.identify_row(event.y)
        if job_id:
            tree.selection_set(job_id); panel.edit_job_id(job_id); show_editor()
            return 'break'
    tree.bind('<Button-1>',click,add='+');tree.bind('<Double-1>',double)


def update_insights(app, config):
    if not hasattr(app, 'dashboard_agenda'): return
    from dashboard_model import snapshot
    jobs=load_jobs();store=TimerStore(ROOT/'logs/messages.sqlite3')
    try:
        states={row['id']:row for row in store.rows()};history=store.history()
    finally:
        store.db.close()
    health={}
    for key,filename in (('gui','gui-heartbeat.json'),('reader','health.json'),('supervisor','supervisor-health.json')):
        try:health[key]=json.loads((ROOT/'logs'/filename).read_text(encoding='utf-8'))
        except (OSError,ValueError):health[key]={}
    model=snapshot(config,jobs,states,history,health,time.time())
    app.dashboard_agenda.render(model)


def modernize_messages(panel):
    """Keep the existing editor controls and callbacks, replace their composition."""
    panel.configure(padding=20)
    children=panel.list_view.winfo_children()
    heading=children[0]
    for child in heading.winfo_children():
        if isinstance(child, ttk.Label): child.configure(text='Programmazioni salvate')
        elif isinstance(child, ttk.Button): child.configure(text='+ Nuova programmazione')
    children[1].pack_forget()
    actions=panel.edit_button.master
    panel.job_list.destroy();actions.destroy()
    old_detail=next(w for w in children if isinstance(w,ttk.Label) and str(w.cget('textvariable'))==str(panel.job_detail))
    old_detail.pack_forget()
    split=ttk.Panedwindow(panel.list_view,orient='vertical');split.pack(fill='both',expand=True)
    listing=ttk.Frame(split);split.add(listing,weight=2)
    listing.rowconfigure(0,weight=1);listing.columnconfigure(0,weight=1)
    panel.job_list=ttk.Treeview(listing,columns=('action','title','state','next','last'),show='headings',selectmode='extended',height=5)
    for col,label in (('action','Comando'),('title','Destinatario'),('state','Stato'),('next','Prossima attività'),('last','Ultima consegna')):panel.job_list.heading(col,text=label,anchor='w')
    panel.job_list.column('action',width=100,minwidth=95,stretch=False,anchor='center')
    bind_job_actions(panel.job_list,panel,lambda:None)
    panel.job_list.grid(row=0,column=0,sticky='nsew')
    panel.job_list.bind('<<TreeviewSelect>>',panel.choose_job)
    scroll=AutoScrollbar(listing,orient='vertical',command=panel.job_list.yview);scroll.grid(row=0,column=1,sticky='ns');panel.job_list.configure(yscrollcommand=scroll.set)
    horizontal=AutoScrollbar(listing,orient='horizontal',command=panel.job_list.xview);horizontal.grid(row=1,column=0,sticky='ew');panel.job_list.configure(xscrollcommand=horizontal.set)
    for col,width in (('title',180),('state',185),('next',180),('last',130)):
        panel.job_list.column(col,width=width,minwidth=65)
    panel.job_list.tag_configure('Attivo',foreground='#254c92')
    panel.job_list.tag_configure('In pausa',foreground=MUTED);panel.job_list.tag_configure('Completato',foreground=MUTED)
    detail=ttk.Frame(split,padding=(0,16));split.add(detail,weight=3)
    actions=ttk.Frame(detail);actions.pack(fill='x',pady=(0,12))
    panel.edit_button=ttk.Button(actions,text='Modifica',command=panel.edit_selected,state='disabled');panel.edit_button.pack(side='left')
    panel.toggle_job=ttk.Button(actions,text='Metti in pausa',command=panel.toggle_selected,state='disabled');panel.toggle_job.pack(side='left',padx=8)
    ttk.Button(actions,text='Metti in pausa tutte',command=panel.pause_all).pack(side='left')
    ttk.Button(actions,text='Registro invii →',command=panel.show_history).pack(side='right',padx=8)
    panel.detail_text=tk.Text(detail,height=5,wrap='word',font=('Segoe UI',10),bg='#f7f9fc',fg=INK,relief='flat',padx=12,pady=12,state='disabled')
    detail_scroll=ttk.Scrollbar(detail,orient='vertical',command=panel.detail_text.yview)
    panel.detail_text.configure(yscrollcommand=detail_scroll.set);detail_scroll.pack(side='right',fill='y')
    panel.detail_text.pack(side='left',fill='both',expand=True)
    # The editor is a separate workspace, not a modal; navigating back keeps WIP.
    panel.editor_title.set('01  Destinatari')
    old_message=panel.message
    initial_text=old_message.get('1.0','end-1c')
    composer=ttk.Frame(panel.write_page)
    composer.pack(fill='both',expand=True,before=old_message,pady=(8,12))
    old_message.destroy()
    tools=ttk.Frame(panel.write_page);tools.pack(fill='x',before=composer,pady=(8,0))
    panel.composer_label.pack_forget()
    panel.composer_label.pack(in_=tools,side='left')
    def choose_saved():
        from message_library import MessageLibraryChooser
        MessageLibraryChooser(panel,panel.use_saved_message)
    save_template=ttk.Button(tools,text='Salva come modello',command=lambda:panel.library_callback());save_template.pack(side='right')
    ttk.Button(tools,text='Usa messaggio salvato',command=choose_saved).pack(side='right',padx=(8,8))
    Tooltip(save_template,'Conserva solo questo testo nella raccolta, con un nome. Non salva la programmazione e non invia messaggi.')
    panel.message=tk.Text(composer,bg='#f7f9fc',fg=INK,insertbackground=BLUE,height=4,width=1,wrap='word',undo=True,font=('Segoe UI',11),relief='flat',padx=12,pady=10,highlightthickness=1,highlightbackground='#dce4f0')
    panel.message.insert('1.0',initial_text)
    composer_scroll=AutoScrollbar(composer,orient='vertical',command=panel.message.yview)
    panel.message.configure(yscrollcommand=composer_scroll.set);composer_scroll.pack(side='right',fill='y');panel.message.pack(side='left',fill='both',expand=True)
    picker=panel.chat_list.master
    catalog_label=ttk.Label(panel.pick_page,textvariable=panel.catalog_status,style='Muted.TLabel',wraplength=720)
    catalog_label.pack(fill='x',pady=(6,8),before=picker)
    catalog_label.bind('<Configure>',lambda e:catalog_label.configure(wraplength=max(120,e.width-4)),add='+')
    panel.status_label.bind('<Configure>',lambda e:panel.status_label.configure(wraplength=max(120,e.width-4)),add='+')
    picker.pack_configure(fill='x',expand=False)
    panel.chat_list.configure(height=6)
    picker_scroll=AutoScrollbar(picker,orient='horizontal',command=panel.chat_list.xview)
    panel.chat_list.configure(xscrollcommand=picker_scroll.set)
    picker_scroll.pack(side='bottom',fill='x',before=panel.chat_list)
    def walk(widget):
        for child in widget.winfo_children():
            yield child
            yield from walk(child)
    switches=[w for w in walk(panel.write_page) if isinstance(w,Switch)]
    if switches:
        area=switches[0].master
        preview=next(w for w in panel.write_page.winfo_children() if isinstance(w,ttk.Label) and str(w.cget('textvariable'))==str(panel.preview))
        area.destroy()
        area=ttk.Frame(panel.write_page);area.pack(fill='x',before=preview)
        input_row=ttk.Frame(area);input_row.pack(fill='x')
        ttk.Label(input_row,text='Frequenza').pack(side='left',padx=(0,12))
        ttk.Combobox(input_row,textvariable=panel.minutes,values=(5,10,15,30,60),width=9).pack(side='left',padx=(0,8))
        ttk.Label(input_row,text='minuti').pack(side='left')
        Switch(area,text='Ripeti nel tempo',variable=panel.repeat,help_text='Acceso: ripete il messaggio all’intervallo scelto. Spento: invia una sola volta. Una bozza o una consegna incerta non vengono reinviate automaticamente.').pack(anchor='w',pady=(10,4))
        Switch(area,text='Invia anche mentre la chat lavora',variable=panel.send_when_active,help_text='Acceso: il messaggio può essere ricevuto o accodato anche se la chat sta elaborando. Spento: attende che la chat sia libera. Vale per entrambe le applicazioni.').pack(anchor='w',pady=4)
        if hasattr(panel, 'send_when_present'):
            Switch(area,text='Invia anche mentre uso il PC',variable=panel.send_when_present,help_text='Acceso: consente l’invio nella chat già aperta senza cambiare chat o prendere il focus, anche mentre usi il PC. Le bozze modificate restano protette. Spento: attende la soglia di inattività configurata.').pack(anchor='w',pady=4)
        preview.pack_configure(pady=(10,6))
    for child in panel.write_page.winfo_children():
        if isinstance(child,ttk.Button) and child.cget('text')=='Attiva programmazione':child.destroy()
        if isinstance(child,ttk.Label) and str(child.cget('text')).startswith('Le bozze restano protette.'):child.destroy()
    panel.recipient_label.pack_forget()
    panel.preview_label.pack_forget()
    summary=ttk.Frame(panel.write_page);summary.pack(fill='x',pady=(12,0))
    summary.columnconfigure(0,weight=3,uniform='summary');summary.columnconfigure(1,weight=2,uniform='summary')
    panel.preview_label.grid(in_=summary,row=0,column=0,sticky='nw',padx=(0,16))
    panel.recipient_label.grid(in_=summary,row=0,column=1,sticky='nw')
    summary.bind('<Configure>',lambda e:(panel.preview_label.configure(wraplength=max(160,int(e.width*0.6)-20)),panel.recipient_label.configure(wraplength=max(140,int(e.width*0.4)-8))),add='+')
    selected_bar=next(child.master for child in panel.pick_page.winfo_children() for child in child.winfo_children() if isinstance(child,ttk.Label) and str(child.cget('textvariable'))==str(panel.target_label))
    for widget in selected_bar.winfo_children():
        if isinstance(widget,ttk.Label):widget.configure(wraplength=460)
    for widget in walk(panel):
        if isinstance(widget,ttk.Label):
            try:
                if int(widget.cget('wraplength'))>720: widget.configure(wraplength=720)
            except (ValueError,tk.TclError): pass


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
    for label,var,callback in (('Rileva domande nelle chat',panel.questions,panel.save_apps),('Risposta automatica alle domande',panel.away,panel.save),('Consenti operazioni sui file delegati',panel.approvals,panel.save_apps),('Recupera scritture bloccate',panel.recovery,panel.save_apps)):
        hints={'Rileva domande nelle chat':'Rileva le richieste di risposta. Da solo non invia risposte: serve anche Risposta automatica alle domande attiva.', 'Risposta automatica alle domande':'Spento: non risponde automaticamente alle domande, neppure durante la tua assenza. Acceso: invia il messaggio di assenza solo dopo l’inattività configurata e con rilevamento domande attivo. Messaggi programmati e consensi file restano indipendenti.', 'Consenti operazioni sui file delegati':'Gestisce solo consensi riconoscibili per i file nelle cartelle autorizzate. Non risponde alle domande e non autorizza eliminazioni, shell, rete o credenziali.', 'Recupera scritture bloccate':'Un solo tentativo di recupero per una scrittura già autorizzata e fallita. Richiede consensi file attivi e rispetta cartelle, pausa e bozze.'}
        Switch(behavior,text=label,variable=var,command=callback,help_text=hints[label]).pack(anchor='w',pady=8)
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
