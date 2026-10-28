"""Frozen executable entry point; workers use the same bundled interpreter."""
import json
import os
import runpy
import sys


def main():
    roles = {"sentinel_launcher", "notification_watch", "notification_backend", "question_supervisor", "adapter_entry", "chat_desktop"}
    args = sys.argv[1:]
    role = "sentinel_launcher"
    if args[:1] == ["--process"]:
        if len(args) < 2 or args[1] not in roles:
            raise SystemExit("Unknown application process")
        role, args = args[1], args[2:]
    from runtime_paths import data_root
    root = data_root()
    root.mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(exist_ok=True)
    # Windowed executables have no inherited standard streams. Worker pipes
    # still remain connected when explicitly provided by their parent.
    if sys.stderr is None:
        sys.stderr = (root / "logs" / "application-errors.log").open("a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = (root / "logs" / "application-output.log").open("a", encoding="utf-8", buffering=1)
    if args[:1] == ["--diagnose"]:
        import tkinter
        import psutil
        import uiautomation
        import winrt.windows.ui.notifications.management
        print(json.dumps({"frozen":bool(getattr(sys,"frozen",False)),"data_root":str(root),"executable":sys.executable,"dependencies_loaded":True}),flush=True)
        return
    if args == ["--smoke-test"]:
        from release_smoke import run
        run()
        return
    if args[:1] == ["--verify-codex-permissions"]:
        from codex_permission_selftest import main as verify
        raise SystemExit(verify(args[1:]))
    if args == ["--remove-startup"]:
        from startup_manager import remove_owned_startup
        remove_owned_startup()
        return
    os.chdir(root)
    sys.argv = [role + ".py", *args]
    runpy.run_module(role, run_name="__main__")


if __name__ == "__main__":
    main()
