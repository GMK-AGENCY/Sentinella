"""One bounded timer step; launched by the common desktop supervisor."""
import json
import argparse
from message_engine import ROOT, TimerStore, tick

if __name__ == "__main__":
    try:
        parser = argparse.ArgumentParser()
        parser.add_argument("--skip-app", action="append", default=[], choices=("codex", "claude"))
        parser.add_argument('--reconcile-only', action='store_true')
        args = parser.parse_args()
        from chat_identity import refresh_saved
        refresh_saved(ROOT)
        config = json.loads((ROOT / "approval-config.json").read_text(encoding="utf-8"))
        config["temporarily_unavailable_apps"] = args.skip_app
        result = tick(config, TimerStore(ROOT / "logs" / "messages.sqlite3"), reconcile_only=args.reconcile_only)
        print(json.dumps(result, ensure_ascii=True), flush=True)
    except Exception as exc:
        print(json.dumps({"action": "error", "error": f"{type(exc).__name__}: {exc}"}), flush=True)
        raise SystemExit(2)
