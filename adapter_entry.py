"""Owned adapter entry point: emit a stack before the coordinator's deadline."""
import faulthandler
import runpy
import sys


if __name__ == "__main__":
    from runtime_paths import SOURCE_ROOT, frozen
    root = SOURCE_ROOT
    script = sys.argv[1]
    if script not in {"question_desktop.py", "approval_desktop.py", "chat_desktop.py", "message_worker.py", "codex_local_sender.py", "file_recovery.py"}:
        raise SystemExit("Unknown native adapter")
    sys.argv = [str(root / script), *sys.argv[2:]]
    faulthandler.enable()
    faulthandler.dump_traceback_later(8, file=sys.stderr)
    try:
        if frozen():
            runpy.run_module(script.removesuffix(".py"), run_name="__main__")
        else:
            runpy.run_path(str(root / script), run_name="__main__")
    finally:
        faulthandler.cancel_dump_traceback_later()
