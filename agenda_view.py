"""Native, vertically scrolling schedule presentation; no delivery side effects."""
from datetime import datetime, timedelta
import tkinter as tk
from tkinter import ttk
from ui_components import AutoScrollbar
from switch_control import Tooltip

BG = '#f3f5f9'
INK = '#17243b'
MUTED = '#66768d'


def stamp(value, full=False):
    return datetime.fromtimestamp(value).astimezone().strftime('%d/%m/%Y %H:%M:%S' if full else '%d/%m %H:%M') if value is not None else ''


def schedule_sections(records, attention, today=None):
    """Keep planned dates, operational waits and paused jobs distinct."""
    today=today or datetime.now().astimezone().date()
    planned={};waiting=[];paused=[];completed=[]
    for record in records:
        if record['status']=='In pausa':paused.append(record)
        elif record.get('status_key') in {'completed','closed_unconfirmed'} or record['status']=='Completato':completed.append(record)
        elif record['job_id'] in attention:waiting.append(record)
        else:
            day=datetime.fromtimestamp(record['when']).astimezone().date() if record.get('when') is not None else today
            planned.setdefault(day,[]).append(record)
    result=[]
    for day,items in sorted(planned.items()):
        label=('Oggi' if day==today else 'Domani' if day==today+timedelta(days=1) else day.strftime('%d/%m/%Y'))
        if day in (today,today+timedelta(days=1)):label+=' · '+day.strftime('%d/%m')
        result.append((str(day),label,items,'planned'))
    for key,label,items in (('waiting','In attesa',waiting),('paused','In pausa',paused),('completed','Completati',completed)):
        if items:result.append((key,label,items,key))
    return result


class AgendaView(tk.Frame):
    def __init__(self, parent, on_edit, on_toggle, on_history, on_new):
        super().__init__(parent, bg=BG)
        self.on_edit, self.on_toggle, self.on_history = on_edit, on_toggle, on_history
        self.model = {}; self.rows = {}; self.order=[]; self.section_headers={}; self.diagnostic = None; self.diagnostic_labels = []
        style=ttk.Style(self)
        style.configure('AgendaAction.TButton',font=('Segoe UI',10),padding=(7,5))
        self.summary=tk.Frame(self,bg=BG); self.summary.pack(fill='x',pady=(0,18))
        self.badges=[]
        for color,fg in (('#e4f3ec','#176d48'),('#e9edf3','#5a6a80'),('#fff0d9','#91580d')):
            label=tk.Label(self.summary,bg=color,fg=fg,font=('Segoe UI',10,'bold'),padx=13,pady=6)
            label.pack(side='left',padx=(0,10));self.badges.append(label)
        ttk.Button(self.summary,text='+ Nuova programmazione',command=on_new,style='Primary.TButton').pack(side='right')
        heading=tk.Frame(self,bg=BG);heading.pack(fill='x',pady=(0,12))
        tk.Label(heading,text='Agenda del giorno',bg=BG,fg=INK,font=('Segoe UI',12,'bold')).pack(side='left')
        tk.Label(heading,text='Ordine: prossimo invio ↓',bg=BG,fg=MUTED,font=('Segoe UI',10)).pack(side='right')
        self.viewport=tk.Frame(self,bg=BG);self.viewport.pack(fill='both',expand=True)
        self.canvas=tk.Canvas(self.viewport,bg=BG,highlightthickness=0,borderwidth=0,takefocus=False)
        self.scroll=AutoScrollbar(self.viewport,orient='vertical',command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.scroll.pack(side='right',fill='y');self.canvas.pack(side='left',fill='both',expand=True)
        self.content=tk.Frame(self.canvas,bg=BG)
        self.content_id=self.canvas.create_window(0,0,window=self.content,anchor='nw')
        self.canvas.bind('<Configure>',self.resize)
        self.content.bind('<Configure>',lambda _e:self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<MouseWheel>',self.wheel)
        self.empty=tk.Label(self.content,text='Nessun messaggio programmato.\nCrea una programmazione per iniziare.',
                            bg=BG,fg=MUTED,font=('Segoe UI',11),padx=30,pady=45,justify='left')
        self.footer=tk.Frame(self,bg=BG);self.footer.pack(side='bottom',fill='x',pady=(12,0),before=self.viewport)
        self.extent=tk.StringVar()
        tk.Label(self.footer,textvariable=self.extent,bg=BG,fg=MUTED,font=('Segoe UI',10)).pack(side='left')
        ttk.Button(self.footer,text='Diagnostica ↗',command=self.show_diagnostics).pack(side='right')

    def wheel(self,event):
        if self.content.winfo_height()>self.canvas.winfo_height():
            self.canvas.yview_scroll(-int(event.delta/120)*2,'units')
        return 'break'

    def resize(self,event):
        self.canvas.itemconfigure(self.content_id,width=event.width)
        for row in self.rows.values():self.layout_row(row)

    def layout_row(self,row):
        available=max(420,self.canvas.winfo_width()-32-235)
        widths=[int(available*ratio) for ratio in (.30,.28,.18,.24)]
        widths[2]=max(126,widths[2])
        widths[3]=max(70,available-sum(widths[:3]))
        for column,width in enumerate(widths,2):row['inner'].columnconfigure(column,weight=0,minsize=width)
        self.fit_text(row['badge'],row['record']['status'],widths[2]-28)
        self.fit_title(row, max(90, widths[0]-10))
        self.fit_text(row['countdown'],row['record']['countdown'] if row['record']['status']!='In pausa' else '—',max(70,widths[1]-10))
        self.fit_text(row['receipt'],'Ricevuto' if row['record'].get('last_confirmed_at') is not None else 'Ricevuta non disponibile',max(70,widths[3]-10))
        for key,column in (('frequency',0),('expected',1),('receipt_date',3)):
            self.fit_text(row[key],row[key].cget('text'),widths[column]-10)
        top=max(30,*(row[key].winfo_reqheight() for key in ('title','countdown','receipt','actions')))
        bottom=max(22,*(row[key].winfo_reqheight() for key in ('frequency','expected','receipt_date')))
        row['inner'].rowconfigure(0,minsize=top)
        row['inner'].rowconfigure(1,minsize=bottom)
        height=max(top+bottom,row['badge'].winfo_reqheight())+18
        if row['frame'].cget('height')!=height:row['frame'].configure(height=height)

    def fit_title(self,row,available):
        self.fit_text(row['title'],row['record']['title'],available)

    def fit_text(self,label,text,available):
        # Status and activity are operational information, never abbreviations.
        # Let Tk wrap the literal text; layout_row grows the complete row.
        label.configure(text=text,wraplength=max(40,available),justify='left')

    def build_row(self,record):
        jid=record['job_id']
        frame=tk.Frame(self.content,bg=BG,height=76,takefocus=True)
        frame.pack(fill='x',padx=16);frame.pack_propagate(False)
        inner=tk.Frame(frame,bg=BG);inner.pack(fill='both',expand=True,pady=(9,8));inner.grid_propagate(False)
        inner.columnconfigure(0,weight=0,minsize=54);inner.columnconfigure(1,weight=0,minsize=17)
        inner.rowconfigure(0,minsize=30);inner.rowconfigure(1,minsize=22)
        clock=tk.Label(inner,text='',width=1,bg=BG,fg=INK,anchor='w',font=('Segoe UI',11,'bold'))
        clock.grid(row=0,column=0,sticky='nsew')
        accent=tk.Canvas(inner,bg=BG,width=16,height=50,highlightthickness=0,borderwidth=0)
        accent.grid(row=0,column=1,rowspan=2,sticky='ns')
        def label(text,col,line,bold=False,color=INK):
            item=tk.Label(inner,text=text,width=1,bg=BG,fg=color,anchor='w',font=('Segoe UI',11 if bold else 10,'bold' if bold else 'normal'))
            item.grid(row=line,column=col,sticky='nsew',padx=(0,10));return item
        title=label(record['title'],2,0,True)
        frequency=label(record['frequency'],2,1,color=MUTED)
        countdown=label('',3,0,True)
        expected=label('',3,1,color=MUTED)
        badge=tk.Label(inner,font=('Segoe UI',10,'bold'),width=1,padx=9,pady=4,anchor='nw')
        badge.grid(row=0,column=4,rowspan=2,sticky='new',padx=(0,10))
        receipt=label('',5,0,color=MUTED);receipt_date=label('',5,1,color=MUTED)
        actions=tk.Frame(inner,bg=BG);actions.grid(row=0,column=6,sticky='e')
        toggle=ttk.Button(actions,width=10,style='AgendaAction.TButton',command=lambda:self.on_toggle(jid));toggle.pack(side='left',padx=(0,5))
        title.configure(cursor='hand2',takefocus=True)
        title.bind('<Button-1>',lambda _e:self.on_edit(jid))
        title.bind('<Return>',lambda _e:self.on_edit(jid))
        tk.Frame(frame,height=1,bg='#e5ebf4').pack(side='bottom',fill='x')
        row=dict(frame=frame,inner=inner,title=title,frequency=frequency,countdown=countdown,expected=expected,badge=badge,
                 receipt=receipt,receipt_date=receipt_date,toggle=toggle,accent=accent,clock=clock,actions=actions,record=record,tooltips=[])
        for widget in (frame,inner,accent,clock,title,frequency,countdown,expected,badge,receipt,receipt_date):
            widget.bind('<Double-1>',lambda _e,key=jid:self.on_edit(key))
            widget.bind('<MouseWheel>',self.wheel)
        frame.bind('<Return>',lambda _e:self.on_edit(jid))
        return row

    def render(self,model):
        self.model=model
        counts=model.get('counts',{});attention={item['job_id']:item for item in model.get('attention',[])}
        enabled=counts.get('enabled',0)
        warnings = sum(item.get('severity') == 'warning' for item in model.get('attention', []))
        for label,text in zip(self.badges,(f"{enabled} {'abilitata' if enabled==1 else 'abilitate'}",f"{counts.get('paused',0)} in pausa",f'{warnings} da controllare')):label.configure(text=text)
        records=model.get('schedule',[]);wanted={row['job_id'] for row in records}
        sections=schedule_sections(records,attention)
        records=[row for _,_,items,_ in sections for row in items]
        groups={row['job_id']:kind for _,_,items,kind in sections for row in items}
        order=[(key,tuple(row['job_id'] for row in items)) for key,_,items,_ in sections];reorder=order!=self.order;self.order=order
        compact=len(records)<=5
        self.viewport.pack_configure(fill='x' if compact else 'both',expand=not compact)
        self.canvas.configure(height=min(400,max(120 if not records else 76,len(records)*76+len(sections)*38)))
        for jid in list(self.rows):
            if jid not in wanted:self.rows.pop(jid)['frame'].destroy()
        self.empty.pack_forget()
        if not records:self.empty.pack(fill='x')
        for record in records:
            jid=record['job_id']
            if jid not in self.rows:self.rows[jid]=self.build_row(record)
            row=self.rows[jid];row['record']=record
            waiting=jid in attention and record['status']!='In pausa'
            warning=waiting and attention[jid].get('severity') == 'warning'
            paused=record['status']=='In pausa'
            full=record['countdown']
            reason=record['reason']
            short='—' if paused else full
            color='#8d5d13' if warning else INK
            row['countdown'].configure(text=short,fg=color)
            when=record.get('when')
            expected=('Nessun nuovo invio' if paused else
                      'Previsto '+stamp(when) if record.get('status_key')=='scheduled' and when is not None else record.get('hint',''))
            row['expected'].configure(text=expected)
            # The full reason and timestamp stay accessible without duplicating prose under the list.
            status=record['status']
            row['badge_text']=status
            row['badge'].configure(text=status,bg='#e9edf3' if paused else '#fff0d9' if warning else '#eaf1fc' if waiting else '#e4f3ec',fg='#5a6a80' if paused else '#91580d' if warning else '#254c92' if waiting else '#176d48')
            background='#fff7e9' if warning else '#edf3fd' if waiting else BG
            for widget in (row['frame'],row['inner'],row['title'],row['frequency'],row['countdown'],row['expected'],row['receipt'],row['receipt_date'],row['clock'],row['actions'],row['accent']):widget.configure(bg=background)
            row['title'].configure(fg=MUTED if paused else INK)
            row['clock'].configure(text=datetime.fromtimestamp(when).astimezone().strftime('%H:%M') if groups[jid]=='planned' and when is not None else '',fg=MUTED if paused else INK)
            row['accent'].delete('all')
            if groups[jid]=='planned':
                row['accent'].create_line(7,0,7,76,fill='#d5e0f0',width=2)
                row['accent'].create_oval(3,7,11,15,fill=BG,outline='#91b6ed',width=1)
            at=record.get('last_confirmed_at')
            row['receipt'].configure(text='Ricevuto' if at is not None else 'Ricevuta non disponibile',fg='#267b55' if at is not None else MUTED)
            row['receipt_date'].configure(text=stamp(at))
            row['toggle'].configure(text='Ⅱ Pausa' if record['enabled'] else '▶ Riprendi')
            # Existing bindings are replaced through reusable tooltip instances.
            if not row['tooltips']:
                row['tooltips']=[Tooltip(row['title'],record['title']),Tooltip(row['badge'],reason),Tooltip(row['countdown'],full+'\n'+reason),Tooltip(row['expected'],expected),Tooltip(row['toggle'],''),Tooltip(row['receipt'],'')]
            for tip,text in zip(row['tooltips'],(record['title'],reason,full+'\n'+reason,expected,'Metti in pausa solo questo messaggio' if record['enabled'] else 'Riprendi questo messaggio','Ricevuto' if at is not None else 'Ricevuta non disponibile')):tip.text=text
            row['frequency'].configure(text=record['frequency']);self.layout_row(row)
        if reorder:
            for header in self.section_headers.values():header.destroy()
            self.section_headers={}
            for row in self.rows.values():row['frame'].pack_forget()
            for key,label,items,kind in sections:
                header=tk.Frame(self.content,bg=BG,height=38);header.pack(fill='x',padx=16);header.pack_propagate(False)
                color='#91580d' if kind=='waiting' and any(item['job_id'] in attention and attention[item['job_id']].get('severity')=='warning' for item in items) else MUTED if kind=='paused' else INK
                tk.Label(header,text=label,bg=BG,fg=color,font=('Segoe UI',11,'bold')).pack(side='left')
                tk.Label(header,text=f'{len(items)} messaggi' if len(items)!=1 else '1 messaggio',bg=BG,fg=MUTED,font=('Segoe UI',10)).pack(side='left',padx=16)
                header.bind('<MouseWheel>',self.wheel)
                self.section_headers[key]=header
                for item in items:self.rows[item['job_id']]['frame'].pack(fill='x',padx=16)
        self.extent.set(f'{len(records)} messaggi · Scorri per vedere gli altri' if len(records)>5 else f'{len(records)} messaggio' if len(records)==1 else f'{len(records)} messaggi')
        self.canvas.configure(height=min(400,max(76,sum(row['frame'].cget('height') for row in self.rows.values())+len(sections)*38)))
        self.refresh_diagnostics()

    def show_diagnostics(self):
        if self.diagnostic and self.diagnostic.winfo_exists():self.diagnostic.lift();return
        self.diagnostic=tk.Toplevel(self);self.diagnostic.title('Sentinella · Diagnostica');self.diagnostic.geometry('620x440');self.diagnostic.minsize(540,400)
        body=ttk.Frame(self.diagnostic,padding=22);body.pack(fill='both',expand=True)
        ttk.Label(body,text='Aggiornamenti dei componenti',font=('Segoe UI',13,'bold')).pack(anchor='w')
        ttk.Label(body,text='La freschezza dei controlli non dimostra una consegna o un consenso riuscito.',wraplength=550).pack(anchor='w',pady=(8,16))
        self.diagnostic_labels=[]
        for _ in range(3):
            variable=tk.StringVar();self.diagnostic_labels.append(variable)
            ttk.Label(body,textvariable=variable,wraplength=550,justify='left').pack(anchor='w',pady=(0,14))
        self.last_diagnostic=tk.StringVar();ttk.Label(body,textvariable=self.last_diagnostic,wraplength=550).pack(anchor='w',pady=(0,12))
        ttk.Button(body,text='Chiudi',command=self.diagnostic.destroy).pack(anchor='e',side='bottom')
        self.refresh_diagnostics()

    def refresh_diagnostics(self):
        if not self.diagnostic or not self.diagnostic.winfo_exists():return
        for variable,item in zip(self.diagnostic_labels,self.model.get('components',[])):
            variable.set(item['name']+' · '+item['label']+'\nUltimo controllo: '+(stamp(item.get('updated'),True) or 'non disponibile'))
        last=self.model.get('last_confirmed')
        self.last_diagnostic.set('Ultima consegna verificata: '+last['title']+' · '+stamp(last['at'],True) if last else 'Nessuna consegna verificata nei dati disponibili.')
