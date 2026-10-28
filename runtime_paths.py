"""Separate immutable application resources from per-user writable state."""
import os
from pathlib import Path
import sys

SOURCE_ROOT = Path(__file__).resolve().parent


def frozen():
    return bool(getattr(sys, "frozen", False))


def data_root():
    # A separate profile is also used by release smoke tests. Child processes
    # inherit it; the live source installation is never migrated implicitly.
    override = os.environ.get("SENTINELLA_DATA_DIR")
    if override:
        root = Path(override)
        if not root.is_absolute():
            raise ValueError("SENTINELLA_DATA_DIR must be an absolute path")
        return root.resolve()
    if frozen():
        return Path(os.environ["LOCALAPPDATA"]) / "Sentinella"
    return SOURCE_ROOT


def command(module, *args, executable=None, source_root=None):
    if module not in {"sentinel_launcher", "notification_watch", "notification_backend", "question_supervisor", "adapter_entry", "chat_desktop"}:
        raise ValueError("Unknown application process")
    exe = str(executable or sys.executable)
    if frozen():
        return [exe, "--process", module, *map(str, args)]
    return [exe, "-u", str(Path(source_root or SOURCE_ROOT) / (module + ".py")), *map(str, args)]
