"""Private, reusable message texts. Saving or using a text never sends it."""
import json
import os
from pathlib import Path
import tempfile
import time
import tkinter as tk
from tkinter import ttk, messagebox
import uuid

from runtime_paths import data_root
from default_message_templates import starter_library
from ui_components import AutoScrollbar, PlaceholderEntry
from switch_control import Tooltip

PERSONAL_COLLECTION = str(uuid.uuid5(uuid.NAMESPACE_URL, 'sentinella://collections/personal'))


class MessageLibraryStore:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else data_root() / "message-library.json"
        self.backup = self.path.with_suffix(self.path.suffix + ".bak")
        self.recovery_notice = ""

    @staticmethod
    def _validate(data):
        if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("items"), list):
            raise ValueError("Formato dei messaggi salvati non riconosciuto")
        seen = set()
        collections=data.get('collections',[])
        if not isinstance(collections,list):raise ValueError('Raccolte non valide')
        collection_ids=set();names=set()
        for collection in collections:
            if not isinstance(collection,dict):raise ValueError('Raccolta non valida')
            try:identity=str(uuid.UUID(collection['id']))
            except (KeyError,TypeError,ValueError,AttributeError):raise ValueError('Identificativo raccolta non valido') from None
            name=collection.get('name')
            if identity!=collection['id'] or identity in collection_ids or not isinstance(name,str) or not name.strip() or len(name.strip())>80 or name.strip().casefold() in names:raise ValueError('Nome o identificativo raccolta non valido')
            collection_ids.add(identity);names.add(name.strip().casefold())
        for item in data["items"]:
            if not isinstance(item, dict):
                raise ValueError("Messaggio salvato non valido")
            try:
                identity = str(uuid.UUID(item["id"]))
            except (KeyError, TypeError, ValueError, AttributeError):
                raise ValueError("Identificativo del messaggio non valido") from None
            if identity != item["id"] or identity in seen:
                raise ValueError("Identificativo del messaggio duplicato o non valido")
            seen.add(identity)
            if item.get('collection_id') is not None and (not isinstance(item['collection_id'],str) or item['collection_id'] not in collection_ids):raise ValueError('Il messaggio appartiene a una raccolta inesistente')
            MessageLibraryStore._validate_text(item.get("title"), item.get("text"))
            updated = item.get("updated_at")
            if isinstance(updated, bool) or not isinstance(updated, (int, float)) or not 0 <= updated < float("inf"):
                raise ValueError("Data del messaggio non valida")
        return data

    @staticmethod
    def _validate_text(title, text):
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > 160:
            raise ValueError("Inserisci un nome da 1 a 160 caratteri")
        if not isinstance(text, str) or not text.strip() or len(text) > 12000:
            raise ValueError("Inserisci un testo da 1 a 12000 caratteri")

    def _read_file(self, path):
        if path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("Archivio messaggi troppo grande")
        return self._validate(json.loads(path.read_text(encoding="utf-8-sig")))

    def _read(self):
        self.recovery_notice = ""
        if not self.path.exists() and not self.backup.exists():
            return starter_library()
        try:
            return self._read_file(self.path)
        except (FileNotFoundError, ValueError, UnicodeError) as exc:
            try:
                recovered = self._read_file(self.backup)
            except (FileNotFoundError, ValueError, UnicodeError):
                raise ValueError("Archivio messaggi illeggibile e nessuna copia di sicurezza valida. I file sono stati conservati.") from exc
            self.recovery_notice = "Caricata la copia di sicurezza. Il prossimo salvataggio ripristinerà l'archivio."
            return recovered

    @staticmethod
    def _atomic(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    def _write(self, previous, changed):
        self._validate(changed)
        if len((json.dumps(changed, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")) > 16 * 1024 * 1024:
            raise ValueError("La raccolta supera 16 MB. Riduci il numero o la lunghezza dei testi.")
        self._atomic(self.backup, previous)
        self._atomic(self.path, changed)
        self.recovery_notice = ""

    @staticmethod
    def _normalized(data):
        changed={**data,'collections':[dict(row) for row in data.get('collections',[])], 'items':[dict(row) for row in data['items']]}
        if 'collections' not in data or any(not row.get('collection_id') for row in changed['items']):
            personal=next((row['id'] for row in changed['collections'] if row['name'].casefold()=='personali'),PERSONAL_COLLECTION)
            if not any(row['id']==personal for row in changed['collections']):changed['collections'].append({'id':personal,'name':'Personali'})
            for row in changed['items']:
                if not row.get('collection_id'):row['collection_id']=personal
        return changed

    def collections(self):
        return sorted(self._normalized(self._read())['collections'],key=lambda row:row['name'].casefold())

    def list(self, query="", collection_id=None):
        data=self._normalized(self._read());rows=data['items']
        names={row['id']:row['name'] for row in data['collections']}
        query = query.casefold().strip()
        return sorted((dict(item) for item in rows if (collection_id is None or item['collection_id']==collection_id) and (not query or query in (item["title"] + "\n" + item["text"]+'\n'+names[item['collection_id']]).casefold())),
                      key=lambda item: (-item["updated_at"], item["title"].casefold(), item["id"]))

    def get(self, item_id):
        return next((item for item in self.list() if item["id"] == item_id), None)

    def save(self, title, text, item_id=None, collection_id=None):
        self._validate_text(title, text)
        previous = self._read()
        normalized=self._normalized(previous)
        if item_id is not None and not any(row["id"] == item_id for row in previous["items"]):
            raise ValueError("Questo messaggio non esiste più. Crea un nuovo messaggio per conservare il testo.")
        old=next((row for row in normalized['items'] if row['id']==item_id),None)
        if collection_id is None:
            collection_id=old['collection_id'] if old else next((row['id'] for row in normalized['collections'] if row['name'].casefold()=='personali'),PERSONAL_COLLECTION)
            if not any(row['id']==collection_id for row in normalized['collections']):normalized['collections'].append({'id':PERSONAL_COLLECTION,'name':'Personali'})
        if not any(row['id']==collection_id for row in normalized['collections']):raise ValueError('Scegli una raccolta esistente')
        item = {"id": item_id or str(uuid.uuid4()), "title": title.strip(), "text": text, "updated_at": time.time(),'collection_id':collection_id}
        changed = {**normalized, "items": [dict(row) for row in normalized["items"] if row["id"] != item["id"]] + [item]}
        self._write(previous, changed)
        return dict(item)

    def delete(self, item_id):
        previous = self._read()
        if not any(row["id"] == item_id for row in previous["items"]):
            raise ValueError("Il messaggio è già stato rimosso")
        changed = {**self._normalized(previous), "items": [dict(row) for row in self._normalized(previous)["items"] if row["id"] != item_id]}
        self._write(previous, changed)

    def save_collection(self,name,collection_id=None):
        if not isinstance(name,str) or not name.strip() or len(name.strip())>80:raise ValueError('Scegli un nome raccolta da 1 a 80 caratteri')
        previous=self._read();data=self._normalized(previous);name=name.strip()
        if collection_id is not None and not any(row['id']==collection_id for row in data['collections']):raise ValueError('Raccolta non trovata')
        if any(row['id']!=collection_id and row['name'].casefold()==name.casefold() for row in data['collections']):raise ValueError('Esiste già una raccolta con questo nome')
        item={'id':collection_id or str(uuid.uuid4()),'name':name}
        data['collections']=[row for row in data['collections'] if row['id']!=item['id']]+[item]
        self._write(previous,data);return dict(item)

    def delete_collection(self,collection_id):
        previous=self._read();data=self._normalized(previous)
        if not any(row['id']==collection_id for row in data['collections']):raise ValueError('Raccolta non trovata')
        if any(row['collection_id']==collection_id for row in data['items']):raise ValueError('La raccolta contiene messaggi. Spostali in un’altra raccolta prima di eliminarla.')
        data['collections']=[row for row in data['collections'] if row['id']!=collection_id]
        self._write(previous,data)


class MessageLibraryPanel(ttk.Frame):
    def __init__(self, parent, on_use):
        super().__init__(parent, padding=20)
        self.on_use = on_use
        self.store = MessageLibraryStore()
        self.selected_id = None
        self.baseline = ("", "")
        self.items = []
        self.rendering = False
        self.query = tk.StringVar()
        self.title_value = tk.StringVar()
        self.status = tk.StringVar()
        self.title_error=tk.StringVar();self.text_error=tk.StringVar()
        self.collection_filter=tk.StringVar(value='Tutte le raccolte');self.collection_value=tk.StringVar()
        self.collection_options={};self.baseline_collection=''
        split=ttk.Frame(self);split.pack(fill='both',expand=True)
        split.columnconfigure(0,weight=3,minsize=190);split.columnconfigure(1,weight=7,minsize=400);split.rowconfigure(0,weight=1)
        listing=ttk.Frame(split,padding=(0,0,16));listing.grid(row=0,column=0,sticky='nsew')
        collection_top=ttk.Frame(listing);collection_top.pack(fill='x',pady=(0,10))
        ttk.Label(collection_top,text='Raccolte',style='Section.TLabel').pack(side='left')
        style=ttk.Style(self);style.configure('LibraryIcon.TButton',padding=(5,6))
        style.configure('LibraryCompact.TButton',padding=(8,7))
        collection_actions=ttk.Frame(listing);collection_actions.pack(fill='x',pady=(0,10))
        self.collection_icons={}
        for icon in ('rename','delete'):
            for state,color in (('normal','#56657a'),('disabled','#aab3c1')):
                image=tk.PhotoImage(master=self,width=20,height=20)
                if icon=='rename':
                    for point in range(5,15):image.put(color,to=(point,18-point,point+3,21-point))
                    image.put(color,to=(3,16,6,18))
                else:
                    for box in ((4,5,16,7),(8,3,12,5),(5,7,7,17),(14,7,16,17),(5,16,16,18),(9,9,10,15),(12,9,13,15)):image.put(color,to=box)
                self.collection_icons[(icon,state)]=image
        self.delete_collection_button=ttk.Button(collection_actions,image=(self.collection_icons['delete','normal'],'disabled',self.collection_icons['delete','disabled']),style='LibraryIcon.TButton',command=self.remove_collection,state='disabled')
        self.delete_collection_button.pack(side='right')
        self.rename_collection_button=ttk.Button(collection_actions,image=(self.collection_icons['rename','normal'],'disabled',self.collection_icons['rename','disabled']),style='LibraryIcon.TButton',command=lambda:self.collection_dialog(rename=True),state='disabled')
        self.rename_collection_button.pack(side='right',padx=4)
        ttk.Button(collection_actions,text='Nuova raccolta',command=self.collection_dialog,style='LibraryCompact.TButton').pack(side='left',fill='x',expand=True)
        self.collection_tooltips=[Tooltip(self.rename_collection_button,'Rinomina raccolta'),Tooltip(self.delete_collection_button,'Elimina raccolta')]
        self.collection_picker=ttk.Combobox(listing,textvariable=self.collection_filter,state='readonly');self.collection_picker.pack(fill='x',pady=(0,10))
        PlaceholderEntry(listing,textvariable=self.query,placeholder='Cerca nel nome o nel testo').pack(fill='x',pady=(0,10))
        list_body=ttk.Frame(listing);list_body.pack(fill='both',expand=True)
        self.tree=ttk.Treeview(list_body,columns=('title',),show='headings',height=7,selectmode='browse')
        self.tree.heading('title',text='Messaggi salvati',anchor='w');self.tree.column('title',width=180,minwidth=80)
        scroll=AutoScrollbar(list_body,orient='vertical',command=self.tree.yview);self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right',fill='y');self.tree.pack(fill='both',expand=True)
        self.empty_hint=tk.StringVar(value='Nessun messaggio. Scrivi a destra e salva, oppure premi Nuovo.')
        ttk.Label(listing,textvariable=self.empty_hint,style='Muted.TLabel',wraplength=210).pack(anchor='w',pady=(10,0))
        editor=ttk.Frame(split);editor.grid(row=0,column=1,sticky='nsew')
        editor_header=ttk.Frame(editor);editor_header.pack(fill='x',pady=(0,14))
        ttk.Label(editor_header,text='Messaggio',style='Section.TLabel').pack(side='left')
        ttk.Button(editor_header,text='+ Nuovo messaggio',command=self.new).pack(side='right')
        fields=ttk.Frame(editor);fields.pack(fill='x');fields.columnconfigure(0,weight=2);fields.columnconfigure(1,weight=3)
        ttk.Label(fields,text='Raccolta').grid(row=0,column=0,sticky='w')
        ttk.Label(fields,text='Nome del messaggio').grid(row=0,column=1,sticky='w',padx=(12,0))
        self.model_collection=ttk.Combobox(fields,textvariable=self.collection_value,state='readonly',width=12)
        self.model_collection.grid(row=1,column=0,sticky='ew',pady=(6,0))
        self.title_entry=ttk.Entry(fields,textvariable=self.title_value,width=18);self.title_entry.grid(row=1,column=1,sticky='ew',padx=(12,0),pady=(6,0))
        ttk.Label(editor,textvariable=self.title_error,foreground='#a44324',wraplength=500).pack(anchor='w',pady=(3,10))
        ttk.Label(editor,text='Testo del messaggio').pack(anchor='w')
        body=ttk.Frame(editor);body.pack(fill='both',expand=True,pady=(6,0))
        self.text=tk.Text(body,height=10,width=1,wrap='word',undo=True,font=('Segoe UI',11),relief='flat',bg='#f7f9fc',fg='#17243b',padx=12,pady=12)
        text_scroll=AutoScrollbar(body,orient='vertical',command=self.text.yview);self.text.configure(yscrollcommand=text_scroll.set)
        text_scroll.pack(side='right',fill='y');self.text.pack(fill='both',expand=True)
        ttk.Label(editor,textvariable=self.text_error,foreground='#a44324',wraplength=500).pack(anchor='w',pady=(3,8))
        footer=ttk.Frame(editor);footer.pack(fill='x',pady=(8,0))
        self.save_button=ttk.Button(footer,text='Salva messaggio',command=self.save,style='Primary.TButton');self.save_button.pack(side='right')
        self.delete_message_button=ttk.Button(footer,text='Elimina messaggio',command=self.remove,state='disabled')
        self.delete_message_button.pack(side='right',padx=(8,8))
        self.use_button=ttk.Button(footer,text='Usa nella programmazione',command=self.use);self.use_button.pack(side='right')
        feedback=ttk.Label(editor,textvariable=self.status,style='Muted.TLabel',wraplength=520)
        def show_feedback(*_):
            if self.status.get():feedback.pack(fill='x',pady=(0,10),before=fields)
            else:feedback.pack_forget()
        self.status.trace_add('write',show_feedback)
        self.tree.bind("<<TreeviewSelect>>", self.select)
        self.query.trace_add("write", lambda *_: self.refresh(reload=False))
        self.collection_filter.trace_add("write",lambda *_:self.refresh(reload=False))
        self.refresh()
        self.baseline_collection=self.collection_value.get()

    def collection_dialog(self,rename=False):
        collection_id=self.collection_options.get(self.collection_filter.get()) if rename else None
        editor_collection_id=self.collection_options.get(self.collection_value.get())
        if rename and collection_id is None:
            self.status.set('Scegli una raccolta nel filtro prima di rinominarla.');return
        if not self._may_leave():return
        window=tk.Toplevel(self);window.title('Rinomina raccolta' if rename else 'Nuova raccolta');window.geometry('460x210');window.resizable(False,False)
        body=ttk.Frame(window,padding=20);body.pack(fill='both',expand=True)
        value=tk.StringVar(value=self.collection_filter.get() if rename else '');error=tk.StringVar()
        ttk.Label(body,text='Nome della raccolta').pack(anchor='w')
        entry=ttk.Entry(body,textvariable=value);entry.pack(fill='x',pady=(8,4))
        ttk.Label(body,textvariable=error,foreground='#a44324',wraplength=410).pack(anchor='w')
        def save():
            try:item=self.store.save_collection(value.get(),collection_id)
            except (OSError,ValueError) as exc:error.set(str(exc));return
            self.refresh();self.collection_filter.set(item['name'])
            self.collection_value.set(next((name for name,key in self.collection_options.items() if key==editor_collection_id),self.collection_value.get()) if rename else item['name'])
            if not rename:self._load();self.tree.selection_remove(*self.tree.selection())
            self.baseline_collection=self.collection_value.get()
            self.status.set('Raccolta rinominata.' if rename else 'Raccolta creata.')
            window.destroy()
        actions=ttk.Frame(body);actions.pack(side='bottom',fill='x')
        ttk.Button(actions,text='Salva raccolta' if rename else 'Crea raccolta',command=save,style='Primary.TButton').pack(side='right')
        ttk.Button(actions,text='Annulla',command=window.destroy).pack(side='right',padx=8)
        entry.bind('<Return>',lambda _event:save())

    def remove_collection(self):
        collection_id=self.collection_options.get(self.collection_filter.get())
        if collection_id is None:self.status.set('Scegli una raccolta da eliminare.');return
        if any(item.get('collection_id')==collection_id for item in self.items):
            self.status.set('La raccolta contiene messaggi. Spostali prima di eliminarla.');return
        if not self._may_leave():return
        if not messagebox.askyesno('Elimina raccolta vuota','Eliminare questa raccolta vuota? Le programmazioni non vengono modificate.',parent=self):return
        try:self.store.delete_collection(collection_id)
        except (OSError,ValueError) as exc:self.status.set(str(exc));return
        self.collection_filter.set('Tutte le raccolte');self.refresh();self._load()
        self.status.set('Raccolta vuota eliminata.')

    def _values(self):
        return self.title_value.get(), self.text.get("1.0", "end-1c")

    def _dirty(self):
        return self._values() != self.baseline or self.collection_value.get()!=self.baseline_collection

    def _may_leave(self):
        if not self._dirty():
            return True
        choice = messagebox.askyesnocancel("Modifiche non salvate", "Vuoi salvare le modifiche a questo messaggio prima di continuare?", parent=self)
        return self.save() if choice is True else choice is False

    def refresh(self, reload=True):
        try:
            if reload:
                self.items = self.store.list()
                self.collection_options={row['name']:row['id'] for row in self.store.collections()}
                self.collection_picker.configure(values=['Tutte le raccolte',*self.collection_options])
                self.model_collection.configure(values=list(self.collection_options))
                if self.collection_value.get() not in self.collection_options:self.collection_value.set(next(iter(self.collection_options),''))
                if self.store.recovery_notice:
                    self.status.set(self.store.recovery_notice)
            query = self.query.get().casefold().strip()
            wanted=self.collection_options.get(self.collection_filter.get())
            for button in (self.rename_collection_button,self.delete_collection_button):button.configure(state='normal' if wanted is not None else 'disabled')
            names={value:key for key,value in self.collection_options.items()}
            rows=[item for item in self.items if (wanted is None or item.get('collection_id')==wanted) and (not query or query in (item['title']+'\n'+item['text']+'\n'+names.get(item.get('collection_id'),'')).casefold())]
            self.empty_hint.set("Nessun messaggio trovato." if query and not rows else "Nessun messaggio. Scrivi a destra e salva." if not rows else f"{len(rows)} messaggi nella raccolta")
            self.rendering = True
            existing = set(self.tree.get_children())
            for index, item in enumerate(rows):
                if item["id"] in existing:
                    self.tree.item(item["id"], values=(item["title"],)); existing.remove(item["id"])
                else:
                    self.tree.insert("", "end", iid=item["id"], values=(item["title"],))
                self.tree.move(item["id"], "", index)
            for item_id in existing:
                self.tree.delete(item_id)
            if self.selected_id in self.tree.get_children():
                self.tree.selection_set(self.selected_id)
        except (OSError, ValueError) as exc:
            self.status.set(str(exc))

        finally:
            self.rendering = False

    def _load(self, item=None):
        self.title_error.set("");self.text_error.set("")
        self.selected_id = item['id'] if item else None
        self.delete_message_button.configure(state='normal' if item else 'disabled')
        name=next((name for name,key in self.collection_options.items() if item and key==item.get('collection_id')),None)
        self.collection_value.set(name or (self.collection_filter.get() if self.collection_filter.get() in self.collection_options else self.collection_value.get()))
        self.title_value.set(item["title"] if item else "")
        self.text.delete("1.0", "end"); self.text.insert("1.0", item["text"] if item else "")
        self.text.edit_reset()
        self.baseline = self._values()
        self.baseline_collection=self.collection_value.get()
        self.status.set('')

    def select(self, _=None):
        selection = self.tree.selection()
        if self.rendering or not selection or selection[0] == self.selected_id:
            return
        target = selection[0]
        if not self._may_leave():
            self.rendering = True
            self.tree.selection_remove(*self.tree.selection())
            if self.selected_id in self.tree.get_children():
                self.tree.selection_set(self.selected_id)
            self.rendering = False
            return
        item = next((row for row in self.items if row["id"] == target), None)
        if item:
            self._load(item)
            # Saving a dirty editor can select the record just saved. Restore
            # the row the user actually asked to open before queued Tk events.
            self.tree.selection_set(target)

    def new(self):
        if self._may_leave():
            self._load()
            self.tree.selection_remove(*self.tree.selection())
            self.status.set('')

    def new_from_text(self, text):
        """Stage a composer's text for naming; never save or send implicitly."""
        try:
            self.store._validate_text("Nuovo testo", text)
        except ValueError as exc:
            self.status.set(str(exc))
            return False
        if not self._may_leave():
            return False
        self._load()
        self.text.insert("1.0", text)
        self.text.edit_reset()
        self.tree.selection_remove(*self.tree.selection())
        self.status.set("Messaggio importato: scegli un nome per salvarlo nella raccolta.")
        return True

    def save(self):
        title,text=self._values()
        self.title_error.set('');self.text_error.set('')
        if self.collection_value.get() not in self.collection_options:
            self.title_error.set('Crea o scegli una raccolta per questo messaggio.');self.model_collection.focus_set();return False
        if not title.strip() or len(title.strip())>160:
            self.title_error.set('Scegli un nome da 1 a 160 caratteri per riconoscere il messaggio.');self.title_entry.focus_set();return False
        if not text.strip() or len(text)>12000:
            self.text_error.set('Inserisci un messaggio da 1 a 12000 caratteri.');self.text.focus_set();return False
        try:
            item = self.store.save(*self._values(), item_id=self.selected_id,collection_id=self.collection_options.get(self.collection_value.get()))
            self.selected_id = item["id"]
            self.baseline = (item["title"], item["text"])
            self.baseline_collection=self.collection_value.get()
            self.title_value.set(item["title"])
            self.refresh()
            self.baseline_collection=self.collection_value.get()
            self.status.set("Messaggio salvato. Nessun invio programmato.")
            return True
        except (OSError, ValueError) as exc:
            self.status.set(str(exc))
            return False

    def use(self):
        text = self.text.get("1.0", "end-1c")
        if not text.strip() or len(text) > 12000:
            self.status.set("Inserisci un testo da 1 a 12000 caratteri da usare nel messaggio.")
            return
        # Reuse does not discard the editor or implicitly write the library.
        try:
            if self.on_use(text) is False:
                self.status.set("Riuso annullato. Il testo nella raccolta è stato conservato.")
                return
        except (OSError, ValueError) as exc:
            self.status.set(str(exc))
            return
        self.status.set("Messaggio copiato nella programmazione.")

    def remove(self):
        if not self.selected_id:
            self.status.set("Seleziona un messaggio da eliminare.")
            return
        if not messagebox.askyesno("Elimina messaggio", "Eliminare questo messaggio dalla raccolta? Le programmazioni già create restano invariate.", parent=self):
            return
        try:
            self.store.delete(self.selected_id)
            self._load()
            self.refresh()
            self.status.set("Messaggio eliminato dalla raccolta.")
        except (OSError, ValueError) as exc:
            self.status.set(str(exc))


class MessageLibraryChooser(tk.Toplevel):
    """Nonblocking chooser: copying a template never changes timer or targets."""
    def __init__(self,parent,on_use):
        super().__init__(parent)
        self.title('Usa messaggio salvato');self.geometry('760x620');self.minsize(620,520)
        self.on_use=on_use;self.store=MessageLibraryStore();self.items=[]
        self.query=tk.StringVar();self.collection_filter=tk.StringVar(value='Tutte le raccolte');self.collection_options={}
        self.notice=tk.StringVar(value='Scegli un messaggio per leggerlo prima di usarlo.')
        body=ttk.Frame(self,padding=16);body.pack(fill='both',expand=True)
        body.columnconfigure(0,weight=1);body.rowconfigure(3,weight=1)
        ttk.Label(body,text='Seleziona un messaggio salvato',font=('Segoe UI',12,'bold')).grid(row=0,column=0,sticky='w',pady=(0,10))
        filters=ttk.Frame(body);filters.grid(row=1,column=0,sticky='ew',pady=(0,10));filters.columnconfigure(1,weight=1)
        self.collection_picker=ttk.Combobox(filters,textvariable=self.collection_filter,state='readonly',width=18)
        self.collection_picker.grid(row=0,column=0,sticky='ew',padx=(0,10))
        PlaceholderEntry(filters,textvariable=self.query,placeholder='Cerca nel nome o nel testo',width=20).grid(row=0,column=1,sticky='ew')
        listing=ttk.Frame(body);listing.grid(row=2,column=0,sticky='ew');listing.columnconfigure(0,weight=1)
        self.tree=ttk.Treeview(listing,columns=('title','collection'),show='headings',height=3,selectmode='browse')
        self.tree.heading('title',text='Nome del messaggio',anchor='w');self.tree.heading('collection',text='Raccolta',anchor='w')
        self.tree.column('title',width=360,minwidth=150);self.tree.column('collection',width=150,minwidth=90)
        listing_scroll=AutoScrollbar(listing,orient='vertical',command=self.tree.yview);self.tree.configure(yscrollcommand=listing_scroll.set)
        self.tree.grid(row=0,column=0,sticky='ew');listing_scroll.grid(row=0,column=1,sticky='ns')
        self.preview_frame=tk.Frame(body,bg='#f7f9fc',borderwidth=0,highlightthickness=1,highlightbackground='#c7d2e2',highlightcolor='#2563eb')
        self.preview_frame.grid(row=3,column=0,sticky='nsew',pady=(12,8));self.preview_frame.rowconfigure(0,weight=1);self.preview_frame.columnconfigure(0,weight=1)
        self.preview=tk.Text(self.preview_frame,width=1,height=5,wrap='word',font=('Segoe UI',11),bg='#f7f9fc',fg='#17243b',relief='flat',borderwidth=0,highlightthickness=0,padx=12,pady=10,state='disabled')
        self.preview_scroll=AutoScrollbar(self.preview_frame,orient='vertical',command=self.preview.yview);self.preview.configure(yscrollcommand=self.preview_scroll.set)
        self.preview.grid(row=0,column=0,sticky='nsew');self.preview_scroll.grid(row=0,column=1,sticky='ns')
        notice=ttk.Label(body,textvariable=self.notice,wraplength=540,justify='left');notice.grid(row=4,column=0,sticky='ew')
        body.bind('<Configure>',lambda event:notice.configure(wraplength=max(280,event.width-32)),add='+')
        controls=ttk.Frame(body);controls.grid(row=5,column=0,sticky='ew',pady=(10,0));controls.columnconfigure(0,weight=1)
        self.cancel_button=ttk.Button(controls,text='Annulla',command=self.destroy);self.cancel_button.grid(row=0,column=1,sticky='e')
        self.use_button=ttk.Button(controls,text='Usa questo messaggio',command=self.use,state='disabled',style='Primary.TButton');self.use_button.grid(row=0,column=2,sticky='e',padx=(8,0))
        self.query.trace_add('write',lambda *_:self.refresh());self.tree.bind('<<TreeviewSelect>>',self.select)
        self.collection_filter.trace_add('write',lambda *_:self.refresh())
        self.refresh()

    def refresh(self):
        try:
            self.collection_options={row['name']:row['id'] for row in self.store.collections()}
            self.collection_picker.configure(values=['Tutte le raccolte',*self.collection_options])
            self.items=self.store.list(self.query.get(),self.collection_options.get(self.collection_filter.get()))
        except (OSError,ValueError) as exc:self.notice.set(str(exc));return
        self.tree.delete(*self.tree.get_children())
        names={key:name for name,key in self.collection_options.items()}
        for item in self.items:self.tree.insert('','end',iid=item['id'],values=(item['title'],names.get(item.get('collection_id'),'Personali')))
        self.use_button.configure(state='disabled')
        self.notice.set('Nessun messaggio trovato. Puoi crearne uno in Messaggi salvati.' if not self.items else 'Scegli un messaggio per vedere il testo. Nessun invio partirà da questa scelta.')
        self.select()

    def select(self,_event=None):
        selected=self.tree.selection();item=next((item for item in self.items if selected and item['id']==selected[0]),None)
        self.preview.configure(state='normal');self.preview.delete('1.0','end')
        if item:self.preview.insert('1.0',item['text'])
        self.preview.configure(state='disabled');self.use_button.configure(state='normal' if item else 'disabled')

    def use(self):
        selected=self.tree.selection();item=next((item for item in self.items if selected and item['id']==selected[0]),None)
        if item and self.on_use(item['text']) is not False:self.destroy()
