"""One explicit save/discard decision for navigation with pending edits."""
import tkinter as tk
from tkinter import ttk


def _choose_unsaved(parent):
    """Return save/discard, or None when the user closes or presses Escape."""
    window = tk.Toplevel(parent)
    window.withdraw()
    window.title("Modifiche non salvate")
    window.resizable(False, False)
    window.transient(parent.winfo_toplevel())
    result = None

    def finish(value=None):
        nonlocal result
        result = value
        window.destroy()

    body = ttk.Frame(window, padding=22)
    body.pack(fill="both", expand=True)
    ttk.Label(body, text="Hai modifiche non salvate.", font=("Segoe UI", 12, "bold")).pack(anchor="w")
    ttk.Label(body, text="Salvale o scartale prima di continuare.\nChiudi questa finestra o premi Esc per restare nell’editor.",
              wraplength=440, justify="left").pack(anchor="w", pady=(10, 20))
    actions = ttk.Frame(body)
    actions.pack(fill="x")
    save_button = ttk.Button(actions, text="Salva modifiche", command=lambda: finish("save"), style="Primary.TButton")
    save_button.pack(side="right")
    ttk.Button(actions, text="Scarta modifiche", command=lambda: finish("discard")).pack(side="right", padx=(0, 10))
    window.protocol("WM_DELETE_WINDOW", finish)
    window.bind("<Escape>", lambda _event: finish())
    window.update_idletasks()
    owner = parent.winfo_toplevel()
    x = max(0, owner.winfo_rootx() + (owner.winfo_width() - window.winfo_reqwidth()) // 2)
    y = max(0, owner.winfo_rooty() + (owner.winfo_height() - window.winfo_reqheight()) // 2)
    window.geometry(f"+{x}+{y}")
    window.deiconify()
    window.grab_set()
    save_button.focus_set()
    window.wait_window()
    return result


def resolve_unsaved(parent, *, dirty, save, discard):
    """Resolve edits before navigation; callbacks run after the dialog closes.

    ``save`` must return True only after a successful save. ``discard`` reloads
    the persisted editor values and may return False if reload fails. A clean
    page never opens a dialog or invokes a callback.
    """
    if not dirty:
        return True
    choice = _choose_unsaved(parent)
    if choice == "save":
        return bool(save())
    if choice == "discard":
        return discard() is not False
    return False
