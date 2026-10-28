"""Reusable Tk controls without polling or third-party dependencies."""
import tkinter as tk
from tkinter import ttk


class PlaceholderEntry(ttk.Entry):
    """A visual hint that never becomes part of the search value."""

    def __init__(self,master=None,placeholder='',**kwargs):
        self.value=kwargs.pop('textvariable',None) or tk.StringVar(master=master)
        super().__init__(master,textvariable=self.value,**kwargs)
        style=ttk.Style(self)
        background=style.lookup(self.cget('style') or 'TEntry','fieldbackground') or '#f7f9fc'
        self.hint=tk.Label(self,text=placeholder,fg='#738197',bg=background,
                           font=kwargs.get('font','TkTextFont'),borderwidth=0,anchor='w',cursor='xterm')
        self.hint.bind('<Button-1>',lambda _event:self.focus_set())
        self.trace=self.value.trace_add('write',self.refresh_hint)
        self.bind('<FocusIn>',self.refresh_hint,add='+')
        self.bind('<FocusOut>',self.refresh_hint,add='+')
        self.bind('<Destroy>',self.cleanup_hint,add='+')
        self.refresh_hint()

    def refresh_hint(self,*_):
        if not self.value.get() and self.focus_get() is not self:
            self.hint.place(x=9,rely=0.5,anchor='w',relwidth=0.9)
        else:self.hint.place_forget()

    def cleanup_hint(self,event):
        if event.widget is self:self.value.trace_remove('write',self.trace)


class AutoScrollbar(ttk.Scrollbar):
    """Hide when the whole view is visible; preserve pack/grid placement.

    Use exactly like ttk.Scrollbar, including yscrollcommand=bar.set. Layout
    changes are coalesced until idle, so transient view ranges during resize do
    not repeatedly hide/show the scrollbar within the same layout pass.
    """

    def __init__(self, master=None, **kwargs):
        super().__init__(master, **kwargs)
        self._needed = True
        self._hidden_by_us = False
        self._layout_manager = None
        self._layout_options = {}
        self._pack_before = None
        self._visibility_callback = None
        self.bind("<Destroy>", self._on_destroy, add=True)

    def set(self, first, last):
        super().set(first, last)
        self._needed = not (float(first) <= 0.0 and float(last) >= 1.0)
        self._schedule_visibility()

    def pack_configure(self, cnf=None, **kwargs):
        super().pack_configure(cnf or {}, **kwargs)
        self._remember_layout("pack")
        self._hidden_by_us = False
        self._schedule_visibility()

    pack = pack_configure

    def grid_configure(self, cnf=None, **kwargs):
        super().grid_configure(cnf or {}, **kwargs)
        self._remember_layout("grid")
        self._hidden_by_us = False
        self._schedule_visibility()

    grid = grid_configure

    def pack_forget(self):
        super().pack_forget()
        self._hidden_by_us = False
        self._layout_manager = None

    forget = pack_forget

    def grid_remove(self):
        super().grid_remove()
        self._hidden_by_us = False

    def grid_forget(self):
        super().grid_forget()
        self._hidden_by_us = False
        self._layout_manager = None

    def _remember_layout(self, manager):
        self._layout_manager = manager
        if manager == "grid":
            self._layout_options = self.grid_info()
        else:
            self._layout_options = self.pack_info()
            siblings = self._layout_options["in"].pack_slaves()
            position = siblings.index(self)
            self._pack_before = siblings[position + 1] if position + 1 < len(siblings) else None

    def _schedule_visibility(self):
        if self._visibility_callback is None:
            self._visibility_callback = self.after_idle(self._apply_visibility)

    def _apply_visibility(self):
        self._visibility_callback = None
        manager = self.winfo_manager()
        if not self._needed and manager in {"pack", "grid"}:
            self._remember_layout(manager)
            if manager == "grid":
                ttk.Scrollbar.grid_remove(self)
            else:
                ttk.Scrollbar.pack_forget(self)
            self._hidden_by_us = True
        elif self._needed and not manager and self._hidden_by_us:
            if self._layout_manager == "grid":
                ttk.Scrollbar.grid_configure(self, **self._layout_options)
            elif self._layout_manager == "pack":
                options = dict(self._layout_options)
                before = self._pack_before
                if before is not None and before.winfo_exists() and before.winfo_manager() == "pack":
                    options["before"] = before
                ttk.Scrollbar.pack_configure(self, **options)
            self._hidden_by_us = False

    def _on_destroy(self, event):
        if event.widget is self and self._visibility_callback is not None:
            self.after_cancel(self._visibility_callback)
            self._visibility_callback = None
