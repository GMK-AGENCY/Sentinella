"""Sentinella window icons from bundled, immutable application resources."""
from pathlib import Path
import ctypes
import sys
import tkinter as tk

APP_USER_MODEL_ID = 'Sentinella.Desktop'


def set_process_identity():
    """Identify this process to the Windows taskbar before creating Tk windows.

    The same ID belongs on the installed shortcuts. Without it, a source
    launch can be grouped under its pythonw host rather than Sentinella.
    This sets process-local Shell metadata only; it does not register, pin,
    install or modify any Windows setting. False means the optional API was
    unavailable or failed, and must not prevent the application from opening.
    """
    if sys.platform != 'win32':
        return False
    try:
        identify = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        identify.argtypes = [ctypes.c_wchar_p]
        identify.restype = ctypes.c_long
        return identify(APP_USER_MODEL_ID) == 0
    except (AttributeError, OSError, TypeError, ValueError, RuntimeError):
        return False


def icon_paths():
    """Resolve source resources or the PyInstaller bundle, never user state."""
    if getattr(sys, "frozen", False) and getattr(sys, "_MEIPASS", None):
        base = Path(sys._MEIPASS)
    else:
        base = Path(__file__).resolve().parent
    return base / "assets" / "sentinella.ico", base / "assets" / "sentinella.png"


def _apply_ico(window, ico):
    if not ico.is_file():
        return False
    applied = False
    # default covers future toplevels; the positional form addresses this
    # window explicitly, including the native large/small Windows icons.
    for apply in (lambda: window.iconbitmap(default=str(ico)),
                  lambda: window.iconbitmap(str(ico))):
        try:
            apply()
            applied = True
        except (tk.TclError, OSError):
            pass
    return applied


def set_window_icon(window):
    """Apply bundled icons and return whether any supported format succeeded.

    Retain all PhotoImages so Tk can choose a suitable size. On Windows apply
    the native multi-size ICO last, to the current window and default future
    windows. PNG remains a fallback if native ICO loading fails. A True result
    confirms an icon API succeeded, not that the Shell taskbar was observed.
    This helper only reads assets and never changes configuration or files.
    """
    ico, png = icon_paths()
    applied = _apply_ico(window, ico) if sys.platform != 'win32' else False
    if png.is_file():
        try:
            photo = tk.PhotoImage(master=window, file=str(png))
            photos = tuple(photo.subsample(factor) for factor in (32, 16, 8, 4, 2)) + (photo,)
            window.iconphoto(True, *photos)
            window._sentinella_icon_image = photo
            window._sentinella_icon_images = photos
            applied = True
        except (tk.TclError, OSError):
            pass
    if sys.platform == 'win32':
        applied = _apply_ico(window, ico) or applied
    return applied
