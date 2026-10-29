"""Compact, responsive operational cards; presentation has no side effects."""
import tkinter as tk
from tkinter import ttk, font as tkfont
from switch_control import Tooltip

BG, INK, MUTED = '#f3f5f9', '#17243b', '#56657a'
TONES = {'good':('#e4f3ec','#176d48'), 'off':('#edf0f5','#66768d'),
         'paused':('#fff0d9','#91580d'), 'waiting':('#fff0d9','#91580d'),
         'warning':('#fff0d9','#91580d'), 'working':('#eaf1fc','#254c92')}


class AutomationCard(tk.Frame):
    def __init__(self,parent,key):
        super().__init__(parent,bg='white',highlightthickness=1,highlightbackground='#e1e7f0',padx=14,pady=12)
        self.key,self.model=key,None
        self.text_fonts={}
        self.last_width=None
        self.inline=False;self.title_inline=False
        top=self.top=tk.Frame(self,bg='white');top.pack(fill='x')
        self.icon=tk.Canvas(top,width=26,height=26,bg='#edf3fd',highlightthickness=0,borderwidth=0)
        self.icon.pack(side='left')
        self.badge=tk.Label(top,font=('Segoe UI',9,'bold'),padx=9,pady=4)
        self.badge.pack(side='right')
        self.title=tk.Label(self,bg='white',fg=INK,font=('Segoe UI',11,'bold'),anchor='w')
        self.title.pack(fill='x',pady=(7,3))
        self.detail=tk.Label(self,bg='white',fg=MUTED,font=('Segoe UI',10),height=2,anchor='nw',justify='left')
        self.detail.pack(fill='x')
        self.meta=tk.Label(self,bg='white',fg='#748298',font=('Segoe UI',9),height=2,anchor='sw',justify='left')
        self.meta.pack(side='bottom',fill='x',pady=(6,0))
        self.hint=Tooltip(self.badge,'')
        self.bind('<Configure>',self.resize)

    def resize(self,event):
        self.layout_text(event.width)

    def layout_text(self,width):
        if width==self.last_width:return
        self.last_width=width
        if self.inline!=self.title_inline:
            self.title.pack_forget()
            if self.inline:self.title.pack(in_=self.top,side='left',fill='x',expand=True,padx=(10,4))
            else:self.title.pack(fill='x',pady=(7,3),before=self.detail)
            self.title_inline=self.inline
        available=max(120,width-30)
        for label in (self.detail,self.meta):
            spec=str(label.cget('font'))
            if spec not in self.text_fonts:self.text_fonts[spec]=tkfont.Font(self,font=spec)
            lines=1 if self.text_fonts[spec].measure(label.cget('text'))<=available else 2
            label.configure(wraplength=available,height=lines)

    def draw_icon(self,color):
        c=self.icon;c.delete('all')
        def line(*points,**options):c.create_line(*points,fill=color,width=1.6,capstyle='round',joinstyle='round',**options)
        if self.key=='notifications':
            c.create_arc(7,4,20,17,start=0,extent=180,style='arc',outline=color,width=1.6)
            line(7,11,7,17,4,20,23,20,20,17,20,11)
            c.create_arc(10,19,17,25,start=180,extent=180,style='arc',outline=color,width=1.6)
        elif self.key=='chats':
            line(5,5,22,5,22,18,13,18,7,23,8,18,5,18,5,5)
            line(9,10,18,10);line(9,14,15,14)
        elif self.key=='replies':
            line(21,21,21,15,17,10,6,10,smooth=True)
            line(11,5,5,10,11,15);line(10,21,16,21)
        else:
            line(8,3,18,3,22,7,22,23,8,23,8,3);line(17,3,17,8,22,8)
            c.create_arc(2,11,16,25,start=40,extent=250,style='arc',outline=color,width=1.6)
            line(2,14,2,19,7,19)

    def render(self,model):
        if model==self.model:return
        self.model=dict(model)
        bg,fg=TONES[model['tone']]
        self.badge.configure(text=model['badge'],bg=bg,fg=fg)
        self.title.configure(text=model['title'])
        self.detail.configure(text=model['detail'])
        self.meta.configure(text=model['meta'])
        self.last_width=None;self.layout_text(self.winfo_width())
        self.hint.text=model.get('hint') or model['detail']+'\n'+model['meta']
        self.icon.configure(bg='#edf0f5' if model['tone']=='off' else '#edf3fd')
        self.draw_icon('#8793a4' if model['tone']=='off' else '#3565b4')


class AutomationOverview(tk.Frame):
    def __init__(self,parent,on_settings):
        super().__init__(parent,bg=BG)
        header=tk.Frame(self,bg=BG);header.pack(fill='x',pady=(0,12))
        tk.Label(header,text='Controlli e automazioni',bg=BG,fg=INK,font=('Segoe UI',12,'bold')).pack(side='left')
        ttk.Button(header,text='Gestisci opzioni →',command=on_settings).pack(side='right')
        self.body=tk.Frame(self,bg=BG);self.body.pack(fill='x')
        self.cards={key:AutomationCard(self.body,key) for key in ('notifications','chats','replies','recovery')}
        self.title_font=tkfont.Font(self,font=('Segoe UI',11,'bold'))
        self.badge_font=tkfont.Font(self,font=('Segoe UI',9,'bold'))
        self.columns=None
        self.layout=None
        self.body.bind('<Configure>',self.reflow)

    def reflow(self,event):
        minimum=max(230,self.title_font.measure('Risposte automatiche')+32)
        columns=4 if event.width>=4*minimum+36 else 2
        inline=columns==2 and event.width/2-6>=max(350,self.title_font.measure('Risposte automatiche')+self.badge_font.measure('Da configurare')+68)
        if (columns,inline)==self.layout:return
        self.layout=(columns,inline)
        self.columns=columns
        for column in range(4):self.body.columnconfigure(column,weight=1 if column<columns else 0,uniform='cards' if column<columns else '')
        for row in range(2):self.body.rowconfigure(row,weight=1 if columns==2 or row==0 else 0,uniform='cardrows' if columns==2 or row==0 else '')
        for index,card in enumerate(self.cards.values()):
            row,column=divmod(index,columns)
            card.inline=inline;card.last_width=None;card.layout_text(card.winfo_width())
            card.grid(row=row,column=column,sticky='nsew',padx=(0,12 if column<columns-1 else 0),pady=(0,12))

    def render(self,models):
        for model in models:self.cards[model['id']].render(model)
