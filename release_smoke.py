"""Opt-in executable smoke test in a separate, clean and paused profile."""
import json
import os
from pathlib import Path
import time
from datetime import datetime


def run(ui_only=False):
    if ui_only and os.environ.get('GITHUB_ACTIONS')!='true':
        raise RuntimeError('Packaged UI verification runs only on the isolated hosted runner')
    from runtime_paths import data_root
    from config_store import ensure_local_files
    root = data_root()
    if not os.environ.get("SENTINELLA_DATA_DIR"):
        raise RuntimeError("Smoke test requires an explicit separate profile")
    ensure_local_files(root)
    config = json.loads((root / "approval-config.json").read_text(encoding="utf-8"))
    jobs = json.loads((root / "messages-config.json").read_text(encoding="utf-8"))["jobs"]
    if not config.get("paused") or config.get("enabled") or config.get("away_mode") or jobs:
        raise RuntimeError("Smoke test requires a clean paused profile without jobs")
    if ui_only:
        import sys
        sys.argv.append('--no-notifications')
    from notification_watch import Monitor
    app = Monitor()
    app.root.withdraw()
    started = time.time()
    result = {}
    if ui_only:
        app.root.deiconify()
        sections=[]
        callback_errors=[]
        original=app.root.report_callback_exception
        def report_error(*args):
            callback_errors.append(str(args[0]));original(*args)
        app.root.report_callback_exception=report_error
        for tab in app.notebook.tabs():
            app.notebook.select(tab);app.root.update()
            if callback_errors:raise RuntimeError('Packaged UI callback failed: '+callback_errors[0])
            section=app.root.nametowidget(tab)
            if not section.winfo_children():raise RuntimeError('Packaged UI section is empty')
            sections.append(app.notebook.tab(tab,'text'))
        if len(sections)<6:raise RuntimeError('Packaged UI sections are incomplete')
        result['ui_sections']=sections
        app.root.withdraw()
    def check():
        try:
            supervisor = json.loads((root / "logs/supervisor-health.json").read_text())
            reader = {} if ui_only else json.loads((root / "logs/health.json").read_text())
            fresh = datetime.fromisoformat(supervisor["updated_at"]).timestamp() >= started - 1
            if fresh and supervisor.get("paused") and (ui_only or reader.get("last_successful_scan")):
                import psutil
                import sys
                children = [psutil.Process(p.pid).exe() for p in (app.worker, app.supervisor) if p is not None]
                if getattr(sys, "frozen", False) and any(Path(p).resolve() != Path(sys.executable).resolve() for p in children):
                    raise RuntimeError("A packaged worker used an external interpreter")
                runtimes = [m.path for m in psutil.Process().memory_maps() if Path(m.path).name.casefold().startswith("python") and m.path.lower().endswith('.dll')]
                result.update(supervisor_paused=True, reader_scan_verified=not ui_only, worker_executables=children, python_runtime_modules=runtimes)
                app.close()
                return
        except (OSError, ValueError, KeyError):
            pass
        if time.time() - started > 12:
            result.update(error="Functional worker checks did not complete within 12 seconds")
            app.close()
        else:
            app.root.after(100, check)
    app.root.after(200, check)
    app.run()
    result.update(paused=config["paused"], jobs=len(jobs))
    (root / "logs/release-smoke.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    if result.get("error"):
        raise RuntimeError(result["error"])
    print(json.dumps(result),flush=True)
