"""Explicit smoke check in an extracted release; all interventions stay paused."""
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config_store import ensure_local_files


if __name__ == "__main__":
    ensure_local_files(ROOT)
    config = json.loads((ROOT / "approval-config.json").read_text(encoding="utf-8"))
    jobs = json.loads((ROOT / "messages-config.json").read_text(encoding="utf-8"))["jobs"]
    if not config.get("paused") or config.get("enabled") or config.get("away_mode") or jobs:
        raise SystemExit("Smoke test allowed only on a clean, paused installation without jobs")
    from notification_watch import Monitor
    app = Monitor()
    app.root.withdraw()
    started = time.time()
    result = {}
    def check_workers():
        try:
            health = json.loads((ROOT / "logs/supervisor-health.json").read_text())
            reader = json.loads((ROOT / "logs/health.json").read_text())
            from datetime import datetime
            fresh = datetime.fromisoformat(health["updated_at"]).timestamp() >= started - 1
            if fresh and health.get("paused") and reader.get("last_successful_scan"):
                result.update(supervisor_paused=True, reader_scan_verified=True)
                app.close()
                return
        except (OSError, ValueError, KeyError):
            pass
        if time.time() - started > 7:
            result['error'] = 'Workers did not publish functional health within 7 seconds'
            app.close()
        else:
            app.root.after(100, check_workers)
    app.root.after(200, check_workers)
    app.run()
    intent = json.loads((ROOT / "logs/exit-intent.json").read_text())
    assert intent["run_id"] == app.run_id
    assert json.loads((ROOT / "approval-config.json").read_text())["paused"]
    assert result.get("supervisor_paused") and result.get("reader_scan_verified"), result
    print(json.dumps({"clean_install_gui_started_and_closed": True, "paused": True, "jobs": 0, **result}))
