"""One bounded timer step; launched by the common desktop supervisor."""
import json
import argparse
from message_engine import ROOT

if __name__ == "__main__":
    try:
        parser = argparse.ArgumentParser()
        parser.add_argument("--skip-app", action="append", default=[], choices=("codex", "claude"))
        parser.add_argument('--reconcile-only', action='store_true')
        args = parser.parse_args()
        # There is one delivery owner. Never fall back to typing/clicking a
        # composer when the native protocol is unavailable or an ACK is lost.
        from native_delivery import run
        result = run(ROOT,reconcile_only=args.reconcile_only)
        print(json.dumps(result, ensure_ascii=True), flush=True)
    except Exception as exc:
        print(json.dumps({"action": "error", "error": f"{type(exc).__name__}: {exc}"}), flush=True)
        raise SystemExit(2)
