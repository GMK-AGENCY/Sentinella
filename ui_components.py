"""Reusable Tk controls without polling or third-party dependencies."""
import tkinter as tk
from tkinter import ttk


def photo_icon(master, name, size=16, color='#17243b'):
    """Draw canonical action symbols into a native, transparent PhotoImage.

    Keep the returned image alive while a widget uses it. No font, image file,
    Pillow installation or external dependency is required at runtime.
    """
    if name not in {'pause', 'play', 'trash'}:
        raise ValueError('Unknown action icon')
    if not isinstance(size, int) or not 12 <= size <= 64:
        raise ValueError('Action icon size must be between 12 and 64 pixels')
    image = tk.PhotoImage(master=master, width=size, height=size)
    def rectangle(left, top, right, bottom):
        scale = size / 20
        bounds = tuple(round(value * scale) for value in (left, top, right, bottom))
        image.put(color, to=bounds)
    if name == 'pause':
        rectangle(5, 4, 8, 17)
        rectangle(12, 4, 15, 17)
    elif name == 'play':
        for y in range(round(size*.2), round(size*.85)):
            extent = round(size*.5 * (1 - abs(y-size*.5)/(size*.35)))
            image.put(color, to=(round(size*.25), y, round(size*.25)+max(1, extent), y+1))
    else:
        rectangle(8, 2, 12, 4)
        rectangle(4, 5, 16, 7)
        rectangle(6, 8, 8, 17)
        rectangle(12, 8, 14, 17)
        rectangle(6, 16, 14, 18)
        rectangle(9, 9, 11, 15)
    return image


class TreeRowActions:
    """Real action buttons over two Treeview cells, only for visible rows.

    Construct after configuring the tree's scroll commands. Call refresh()
    after inserting/updating/deleting rows; scroll and geometry changes trigger
    it automatically. Callbacks receive stable item IDs, never row indices.
    is_paused(iid) returns True/False, or None for an unavailable/deleted job.
    This component does not select rows, edit data or implement confirmation.
    """
    def __init__(self, tree, *, toggle_column='toggle', delete_column='delete',
                 on_toggle, on_delete, is_paused):
        self.tree = tree
        self.toggle_column, self.delete_column = toggle_column, delete_column
        for column in (toggle_column, delete_column):
            tree.column(column)  # Fail early on an integration error.
        self.on_toggle, self.on_delete, self.is_paused = on_toggle, on_delete, is_paused
        self.icons = {name: photo_icon(tree, name) for name in ('pause', 'play', 'trash')}
        self.buttons = {}
        self._cells = {}
        self._pending = None
        self._dead = False
        self._bindings = []
        self._scroll_originals = {}
        self._scroll_hooks = {}
        style = ttk.Style(tree)
        style.configure('RowAction.TButton', padding=(6, 2), font=('Segoe UI', 10))
        for sequence in ('<Configure>', '<Map>', '<Expose>', '<<TreeviewSelect>>',
                         '<<TreeviewOpen>>', '<<TreeviewClose>>', '<MouseWheel>',
                         '<ButtonRelease-1>', '<KeyRelease>'):
            self._bindings.append((sequence, tree.bind(sequence, self.refresh, add='+')))
        self._bindings.append(('<Unmap>', tree.bind('<Unmap>', self._unmap, add='+')))
        self._bindings.append(('<Destroy>', tree.bind('<Destroy>', self._tree_destroyed, add='+')))
        for axis in ('x', 'y'):
            option = axis + 'scrollcommand'
            self._scroll_originals[axis] = str(tree.cget(option))
            tree.configure(**{option: lambda first, last, a=axis: self._scroll(a, first, last)})
            self._scroll_hooks[axis] = str(tree.cget(option))
        self.refresh()

    def _scroll(self, axis, first, last):
        original = self._scroll_originals[axis]
        if original:
            self.tree.tk.call(*self.tree.tk.splitlist(original), first, last)
        self.refresh()

    def refresh(self, *_):
        if not self._dead and self._pending is None:
            self._pending = self.tree.after_idle(self._layout)

    def _visible_rows(self):
        height = self.tree.winfo_height()
        style = ttk.Style(self.tree)
        rowheight = int(style.lookup(self.tree.cget('style') or 'Treeview', 'rowheight') or 20)
        # Half-row samples include partially visible edge rows without scanning
        # an arbitrarily large catalog or allocating a widget for each record.
        return dict.fromkeys(self.tree.identify_row(y)
                             for y in range(0, height, max(1, rowheight//2)))

    def _layout(self):
        self._pending = None
        if self._dead:
            return
        if not self.tree.winfo_ismapped():
            self._clear()
            return
        visible = {}
        for iid in self._visible_rows():
            if not iid or not self.tree.bbox(iid):
                continue
            paused = self.is_paused(iid)
            if paused is not None:
                visible[iid] = bool(paused)
        for iid in list(self.buttons):
            if iid not in visible:
                self._drop(iid)
        for iid, paused in visible.items():
            if iid not in self.buttons:
                self._create(iid)
            row = self.buttons[iid]
            row['toggle'].configure(text='Riprendi' if paused else 'Pausa',
                                    image=self.icons['play' if paused else 'pause'])
            row['toggle_tip'].text = 'Riprendi questo messaggio' if paused else 'Metti in pausa solo questo messaggio'
            for action, column in (('toggle', self.toggle_column), ('delete', self.delete_column)):
                self._place(iid, action, column)

    def _create(self, iid):
        from switch_control import Tooltip
        toggle = ttk.Button(self.tree, text='Pausa', compound='left', takefocus=True,
                            style='RowAction.TButton', command=lambda key=iid: self._invoke(key, 'toggle'))
        delete = ttk.Button(self.tree, text='Elimina programmazione', image=self.icons['trash'],
                            compound='none', takefocus=True, style='RowAction.TButton',
                            command=lambda key=iid: self._invoke(key, 'delete'))
        for action, button in (('toggle', toggle), ('delete', delete)):
            button.bind('<Return>', lambda _event, key=iid, kind=action: self._invoke_key(key, kind))
            button.bind('<MouseWheel>', self._wheel)
        self.buttons[iid] = {'toggle': toggle, 'delete': delete,
                             'toggle_tip': Tooltip(toggle, ''),
                             'delete_tip': Tooltip(delete, 'Elimina questa programmazione')}

    def _place(self, iid, action, column):
        button = self.buttons[iid][action]
        cell = self.tree.bbox(iid, column)
        width, height = self.tree.winfo_width(), self.tree.winfo_height()
        if not cell or cell[0] < 0 or cell[1] < 0 or cell[0]+cell[2] > width or cell[1]+cell[3] > height:
            button.place_forget()
            self._cells.pop((iid, action), None)
            return
        x, y, w, h = cell
        geometry = (x+3, y+2, max(1, w-6), max(1, h-4))
        previous = self._cells.get((iid, action))
        if previous != tuple(cell) or button.winfo_manager() != 'place':
            button.place(x=geometry[0], y=geometry[1], width=geometry[2], height=geometry[3])
            button.lift()
            self._cells[(iid, action)] = tuple(cell)

    def _invoke_key(self, iid, action):
        self._invoke(iid, action)
        return 'break'

    def _wheel(self, event):
        # Child buttons otherwise swallow the table's native wheel behavior.
        self.tree.event_generate('<MouseWheel>', delta=event.delta, state=event.state)
        return 'break'

    def _invoke(self, iid, action):
        if self._dead or iid not in self.buttons or not self.tree.exists(iid):
            return
        column = self.toggle_column if action == 'toggle' else self.delete_column
        # A queued click must never operate on a stale visual position after a
        # scroll/reorder, nor on a row whose backing job was already removed.
        if tuple(self.tree.bbox(iid, column)) != self._cells.get((iid, action)) or self.is_paused(iid) is None:
            self.refresh()
            return
        callback = self.on_toggle if action == 'toggle' else self.on_delete
        callback(iid)
        self.refresh()

    def _drop(self, iid):
        row = self.buttons.pop(iid)
        for action in ('toggle', 'delete'):
            if row[action].winfo_exists():
                row[action].destroy()
            self._cells.pop((iid, action), None)

    def _clear(self):
        for iid in list(self.buttons):
            self._drop(iid)

    def _unmap(self, _event=None):
        self._clear()

    def _tree_destroyed(self, event):
        if event.widget is self.tree:
            self.destroy()

    def destroy(self):
        if self._dead:
            return
        self._dead = True
        if self._pending is not None:
            self.tree.after_cancel(self._pending)
            self._pending = None
        alive = bool(self.tree.winfo_exists())
        self._clear()
        if alive:
            for sequence, binding in self._bindings:
                self.tree.unbind(sequence, binding)
            for axis, hook in self._scroll_hooks.items():
                option = axis+'scrollcommand'
                if str(self.tree.cget(option)) == hook:
                    self.tree.configure(**{option: self._scroll_originals[axis]})
                self.tree.deletecommand(hook)


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
