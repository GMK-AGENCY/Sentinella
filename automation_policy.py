"""Independent scopes and a common non-interference gate for desktop actions."""
import ctypes
from ctypes import wintypes
import json

from runtime_paths import data_root
ROOT = data_root()


def load_config():
    from config_store import ensure_local_files
    ensure_local_files(ROOT)
    return json.loads((ROOT / "approval-config.json").read_text(encoding="utf-8"))


def app_enabled(config, app, kind):
    data = config["apps"][app]
    if kind in config.get("shared_controls", {}):
        return bool(data.get("process_path")) and config["shared_controls"][kind]
    key = "questions_enabled" if kind == "questions" else "approvals_enabled"
    return data.get(key, data.get("enabled", False))


def target_titles(config, app, kind):
    data = config["apps"][app]
    key = "question_target_titles" if kind == "questions" else "approval_target_titles"
    return data.get(key, data.get("target_titles", []))


def title_allowed(config, app, title, kind):
    titles = target_titles(config, app, kind)
    return not titles or title in titles


def idle_seconds():
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]
    info = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO), 0)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0  # A failed observation never grants permission to take focus.
    ctypes.windll.kernel32.GetTickCount.restype = wintypes.DWORD
    return ((ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000


def action_block_reason(config, observed_idle=None):
    if config.get("paused", False):
        return "Sentinella in pausa globale"
    threshold = float(config.get("idle_seconds_before_actions", 30))
    if threshold > 0:
        seconds = idle_seconds() if observed_idle is None else observed_idle
        if seconds < threshold:
            return f"PC in uso: attendo {threshold:g} secondi senza mouse o tastiera"
    return None


def live_block_reason():
    return action_block_reason(load_config())


def typing_keys_down():
    """Do not confuse moving the mouse elsewhere with typing in a composer."""
    keys = [8, 9, 13, 32, 46, *range(48, 91), *range(96, 112), *range(186, 223)]
    try:
        return any(ctypes.windll.user32.GetAsyncKeyState(key) & 0x8000 for key in keys)
    except (AttributeError, OSError):
        return True


def native_delivery_config(read_config=load_config, foreground_exe=None, app='codex', allow_present=None):
    """Shared policy for an already-open background chat; never grants focus."""
    config = read_config()
    if allow_present is False or config.get('paused') or not action_block_reason(config):
        return config
    if allow_present is True:
        return {**config, 'idle_seconds_before_actions': 0, 'native_background_delivery': True}
    if foreground_exe is None:
        import psutil
        from desktop_identity import foreground_process_id
        try:
            foreground_exe = psutil.Process(foreground_process_id()).exe()
        except psutil.Error:
            return config
    from desktop_identity import executable_matches
    expected = config.get('apps', {}).get(app, {}).get('process_path')
    if not expected or executable_matches(foreground_exe, expected):
        return config
    return {**config, 'idle_seconds_before_actions': 0, 'native_background_delivery': True}
