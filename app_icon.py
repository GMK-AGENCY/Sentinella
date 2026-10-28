"""Sentinella window icons from bundled, immutable application resources."""
from pathlib import Path
import sys
import tkinter as tk


def icon_paths():
    """Resolve source resources or the PyInstaller bundle, never user state."""
    if getattr(sys, "frozen", False) and getattr(sys, "_MEIPASS", None):
        base = Path(sys._MEIPASS)
    else:
        base = Path(__file__).resolve().parent
    return base / "assets" / "sentinella.ico", base / "assets" / "sentinella.png"


def set_window_icon(window):
    """Apply bundled icons and return whether any supported format succeeded.

    Retain PhotoImage on its Tk window so Python cannot collect the icon. The
    .ico provides native Windows sizes; PNG is a Tk fallback on other systems.
    This helper only reads assets and never changes configuration or files.
    """
    ico, png = icon_paths()
    applied = False
    if ico.is_file():
        try:
            window.iconbitmap(default=str(ico))
            applied = True
        except (tk.TclError, OSError):
            pass
    if png.is_file():
        try:
            photo = tk.PhotoImage(master=window, file=str(png))
            window.iconphoto(True, photo)
            window._sentinella_icon_image = photo
            applied = True
        except (tk.TclError, OSError):
            pass
    return applied
