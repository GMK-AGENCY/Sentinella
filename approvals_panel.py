"""Explicit project roots for automatic routine file approvals."""
import json
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from message_engine import ROOT
from switch_control import Switch
from ui_components import AutoScrollbar


class ApprovalsPanel(ttk.Frame):
    def __init__(self, parent, enabled_variable=None):
        super().__init__(parent, padding=(0, 4, 0, 0))
        config = self.config()
        self.enabled = enabled_variable if enabled_variable is not None else tk.BooleanVar(value=config.get("enabled", False))
        if enabled_variable is None:
            Switch(self, text="Approva letture e scritture nelle cartelle delegate", variable=self.enabled, command=self.save,help_text='Acceso: gestisce consensi file riconoscibili soltanto nelle cartelle elencate. Spento: lascia i consensi all’utente. Nessuna autorizzazione a eliminazioni, shell o rete.').pack(anchor="w")
        self.prefer_always = tk.BooleanVar(value=config.get("prefer_always", False))
        Switch(self, text="Preferisci il consenso per sessione/progetto, quando esplicitamente previsto", variable=self.prefer_always, command=self.save,help_text='Acceso: preferisce il consenso valido per la sessione o il progetto solo se la richiesta lo offre esplicitamente e riguarda le cartelle autorizzate. Spento: usa il consenso per la singola operazione.').pack(anchor="w", pady=(4, 16), fill="x")
        listing_header = ttk.Frame(self)
        listing_header.pack(fill="x", pady=(0, 8))
        ttk.Label(listing_header, text="Percorsi autorizzati", font=("Segoe UI", 10, "bold")).pack(side="left")
        self.root_count = tk.StringVar()
        ttk.Label(listing_header, textvariable=self.root_count, style="Muted.TLabel").pack(side="right")
        listing = ttk.Frame(self)
        listing.pack(fill="x")
        listing.columnconfigure(0, weight=1)
        self.roots = tk.Listbox(listing, height=8, font=("Segoe UI", 10), selectmode="browse", exportselection=False, activestyle="dotbox", relief="flat", bg="#f7f9fc", fg="#17243b", selectbackground="#e5edff", selectforeground="#173a85", highlightthickness=1, highlightbackground="#dbe2ea", highlightcolor="#2563eb")
        self.roots.grid(row=0, column=0, sticky="nsew")
        vertical = AutoScrollbar(listing, orient="vertical", command=self.roots.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = AutoScrollbar(listing, orient="horizontal", command=self.roots.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.roots.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        for path in config.get("allowed_project_roots", []):
            self.roots.insert("end", path)
        buttons = ttk.Frame(self)
        buttons.pack(fill="x", pady=(12, 0))
        ttk.Button(buttons, text="Aggiungi cartella", command=self.add).pack(side="left")
        self.remove_button = ttk.Button(buttons, text="Rimuovi selezionata", command=self.remove, state="disabled")
        self.remove_button.pack(side="left", padx=(8, 0))
        self.selected_path = tk.StringVar(value="Seleziona una cartella per leggere il percorso completo. Sono incluse le sottocartelle.")
        self.path_label = ttk.Label(self, textvariable=self.selected_path, wraplength=650, style="Muted.TLabel")
        self.path_label.pack(anchor="w", fill="x", pady=(10, 0))
        self.roots.bind("<<ListboxSelect>>", lambda _: self.refresh_selection())
        self.bind("<Configure>", lambda event: self.path_label.configure(wraplength=max(240, event.width - 8)), add="+")
        self.refresh_selection()
        self.status = tk.StringVar(value="Le modifiche alla delega sono salvate subito.")
        ttk.Label(self, textvariable=self.status, wraplength=900, style="Muted.TLabel").pack(anchor="w", pady=(8, 0))
        ttk.Label(self, text="Solo letture e scritture con percorsi verificati. Restano esclusi eliminazioni, shell, rete e accessi.", wraplength=900, style="Muted.TLabel").pack(anchor="w", pady=(6, 0))

    def refresh_selection(self):
        count = self.roots.size()
        self.root_count.set(f"{count} cartella" if count == 1 else f"{count} cartelle")
        selected = self.roots.curselection()
        self.remove_button.configure(state="normal" if selected else "disabled")
        self.selected_path.set(self.roots.get(selected[0]) if selected else "Seleziona una cartella per leggere il percorso completo. Sono incluse le sottocartelle.")

    def config(self):
        return json.loads((ROOT / "approval-config.json").read_text(encoding="utf-8"))

    def save(self):
        config = self.config()
        config.update(enabled=self.enabled.get(), prefer_always=self.prefer_always.get(), allowed_project_roots=list(self.roots.get(0, "end")))
        path = ROOT / "approval-config.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
        self.status.set("Consensi file attivi nelle cartelle elencate." if self.enabled.get() else "Consensi file in pausa.")

    def add(self):
        folder = filedialog.askdirectory(parent=self, title="Scegli una cartella già autorizzata per i file di progetto", mustexist=True)
        if folder:
            path = str(Path(folder).resolve())
            if path not in self.roots.get(0, "end"):
                self.roots.insert("end", path)
                self.save()
                self.refresh_selection()

    def remove(self):
        selected = self.roots.curselection()
        if selected:
            index=selected[0];path=self.roots.get(index)
            if not messagebox.askyesno('Rimuovi cartella autorizzata',f'Rimuovere «{path}» dalle cartelle autorizzate?\n\nI file sul PC non verranno eliminati.',parent=self,default='no'):return False
            self.roots.delete(index)
            try:self.save()
            except (OSError,ValueError) as exc:
                self.roots.insert(index,path);self.roots.selection_set(index)
                self.status.set(f'Rimozione non riuscita: {exc}');self.refresh_selection();return False
            self.refresh_selection()
            return True
        return False
