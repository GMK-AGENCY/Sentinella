"""A compact toggle with keyboard support and no image dependencies."""
import tkinter as tk
from tkinter import ttk


class Tooltip:
    def __init__(self, widget, text):
        self.widget, self.text, self.window, self.pending = widget, text, None, None
        widget.bind('<Enter>', self.schedule, add='+')
        widget.bind('<Leave>', self.hide, add='+')
        widget.bind('<FocusIn>', self.schedule, add='+')
        widget.bind('<FocusOut>', self.hide, add='+')
        widget.bind('<Escape>', self.hide, add='+')
        widget.bind('<ButtonPress>', self.hide, add='+')
        widget.bind('<Destroy>', self.hide, add='+')

    def schedule(self, _event=None):
        self.hide()
        self.pending = self.widget.after(450, self.show)

    def show(self):
        self.pending = None
        if not self.widget.winfo_exists() or self.window:
            return
        self.window = tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.attributes('-topmost', True)
        ttk.Label(tip, text=self.text, wraplength=360, padding=12, background='#17243b', foreground='white',
                  font=('Segoe UI', 10), justify='left').pack()
        tip.update_idletasks()
        x = min(self.widget.winfo_rootx(), self.widget.winfo_screenwidth() - tip.winfo_width() - 12)
        y = min(self.widget.winfo_rooty() + self.widget.winfo_height() + 6, self.widget.winfo_screenheight() - tip.winfo_height() - 12)
        tip.geometry(f'+{max(0,x)}+{max(0,y)}')

    def hide(self, _event=None):
        if self.pending:
            self.widget.after_cancel(self.pending)
            self.pending = None
        if self.window:
            self.window.destroy()
            self.window = None


class Switch(ttk.Frame):
    def __init__(self, parent, text, variable, command=None, help_text=None):
        super().__init__(parent)
        self.variable = variable
        self.command = command
        self.canvas = tk.Canvas(self, width=38, height=24, highlightthickness=0, background="white", cursor="hand2", takefocus=True)
        self.canvas.pack(side="left")
        self.label = ttk.Label(self, text=text, cursor="hand2", wraplength=850)
        self.label.pack(side="left", padx=(10, 0), fill="x", expand=True)
        if help_text:
            self.tooltips = [Tooltip(widget, help_text) for widget in (self.canvas, self.label)]
        self.bind("<Configure>", self.fit_label, add="+")
        self.canvas.bind("<Button-1>", self.toggle)
        self.label.bind("<Button-1>", self.toggle)
        self.canvas.bind("<space>", self.toggle)
        self.canvas.bind("<Return>", self.toggle)
        self.canvas.bind("<FocusIn>", lambda _: self.draw(), add='+')
        self.canvas.bind("<FocusOut>", lambda _: self.draw(), add='+')
        self.trace = variable.trace_add("write", lambda *_: self.draw())
        self.bind("<Destroy>", self.cleanup, add=True)
        self.draw()

    def fit_label(self, event):
        # Labels wrap inside their actual card, including at minimum width.
        # No canvas or indicator geometry changes when a line wraps.
        if event.width > 60:
            self.label.configure(wraplength=max(60, event.width - 50))

    def cleanup(self, event):
        if event.widget is self:
            self.variable.trace_remove("write", self.trace)

    def draw(self):
        self.canvas.delete("all")
        on = bool(self.variable.get())
        color = "#2456c5" if on else "#a8b3c2"
        self.canvas.create_oval(1, 3, 19, 21, fill=color, outline=color)
        self.canvas.create_rectangle(10, 3, 27, 21, fill=color, outline=color)
        self.canvas.create_oval(19, 3, 37, 21, fill=color, outline=color)
        left = 21 if on else 3
        self.canvas.create_oval(left, 5, left + 14, 19, fill="white", outline="white")
        if self.canvas.focus_get() is self.canvas:
            self.canvas.create_rectangle(0, 0, 37, 23, outline="#2456c5", dash=(2, 2))

    def toggle(self, _=None):
        self.canvas.focus_set()
        self.variable.set(not self.variable.get())
        if self.command:
            self.command()
        return "break"
