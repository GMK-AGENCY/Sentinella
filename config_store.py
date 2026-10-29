"""Safe first-run files; existing user configuration is never overwritten."""
import json
from pathlib import Path


def default_config():
    from message_templates import AWAY_REPLY
    return {"enabled": False, "away_mode": False, "away_reply": AWAY_REPLY,
            "prefer_always": False, "automatic_command_approval": False,
            "allowed_project_roots": [], "paused": True,
            "idle_seconds_before_actions": 30, "notification_poll_seconds": 30,
            "chat_scan_seconds": 900, "start_with_windows": False, "automatic_file_recovery": False,
            "apps": {app: {"enabled": False, "process_path": "", "questions_enabled": False,
                           "approvals_enabled": False, "question_target_titles": [], "approval_target_titles": []}
                     for app in ("codex", "claude")}}


def ensure_local_files(root):
    root = Path(root)
    for name, value in (("approval-config.json", default_config()), ("messages-config.json", {"jobs": []})):
        try:
            with (root / name).open("x", encoding="utf-8") as out:
                json.dump(value, out, ensure_ascii=False, indent=2)
                out.write("\n")
        except FileExistsError:
            pass


def detect_running_apps():
    import psutil
    found = {"codex": set(), "claude": set()}
    for process in psutil.process_iter(["exe"]):
        try:
            path = process.info["exe"] or ""
            lowered = path.casefold()
            if "\\windowsapps\\openai.codex_" in lowered and lowered.endswith("\\app\\chatgpt.exe"):
                found["codex"].add(path)
            if "\\windowsapps\\claude_" in lowered and lowered.endswith("\\app\\claude.exe"):
                found["claude"].add(path)
        except psutil.Error:
            continue
    return {app: next(iter(paths)) for app, paths in found.items() if len(paths) == 1}
