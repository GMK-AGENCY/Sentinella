"""Current-user Windows Startup shortcut. No administrator rights required."""
import ctypes
import json
from pathlib import Path
from automation_policy import ROOT, load_config
from runtime_paths import frozen
import sys


def startup_target():
    if frozen():
        return str(Path(sys.executable).resolve()), "--background"
    return str(ROOT / ".venv/Scripts/pythonw.exe"), '"' + str(ROOT / "sentinel_launcher.py") + '" --background'


def startup_path():
    buffer = ctypes.create_unicode_buffer(32768)
    if ctypes.windll.shell32.SHGetFolderPathW(None, 7, None, 0, buffer) != 0:
        raise OSError("Cartella di avvio Windows non disponibile")
    return Path(buffer.value) / "Sentinella.lnk"


def shell():
    from comtypes.client import CreateObject
    return CreateObject("WScript.Shell", dynamic=True)


def owns_shortcut(shortcut):
    if frozen():
        target, arguments = startup_target()
        return str(shortcut.TargetPath).casefold() == target.casefold() and str(shortcut.Arguments).strip() == arguments
    target = str(ROOT / ".venv/Scripts/pythonw.exe").casefold()
    script = str(ROOT / "sentinel_launcher.py").casefold()
    arguments = str(shortcut.Arguments).split(" --", 1)[0].strip().strip('"').casefold()
    return str(shortcut.TargetPath).casefold() == target and arguments == script


def startup_enabled():
    path = startup_path()
    if not path.is_file():
        return False
    try:
        return owns_shortcut(shell().CreateShortcut(str(path)))
    except (OSError, AttributeError):
        return False


def remove_owned_startup():
    path = startup_path()
    if path.is_file() and owns_shortcut(shell().CreateShortcut(str(path))):
        path.unlink()


def set_startup(enabled, path=None, client=None):
    path = Path(path) if path else startup_path()
    if path.exists():
        client = client or shell()
        if not owns_shortcut(client.CreateShortcut(str(path))):
            raise ValueError("Il collegamento di avvio appartiene a un'altra installazione: nessuna modifica eseguita")
    if enabled:
        client = client or shell()
        shortcut = client.CreateShortcut(str(path))
        shortcut.TargetPath, shortcut.Arguments = startup_target()
        shortcut.WorkingDirectory = str(ROOT)
        shortcut.Description = "Sentinella: avvio con Windows e ripristino dopo crash"
        shortcut.IconLocation = str(shortcut.TargetPath) + ",0"
        shortcut.Save()
    elif path.exists():
        client = client or shell()
        shortcut = client.CreateShortcut(str(path))
        if not owns_shortcut(shortcut):
            raise ValueError("Il collegamento esistente non appartiene a questa Sentinella")
        path.unlink()
    config = load_config()
    config["start_with_windows"] = bool(enabled)
    temporary = (ROOT / "approval-config.json").with_suffix(".tmp")
    temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(ROOT / "approval-config.json")
    return path
