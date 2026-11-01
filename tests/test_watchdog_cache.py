import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from sentinel_launcher import HeartbeatWatch, Launcher, read_json, request_window


class HeartbeatWatchTests(unittest.TestCase):
    def test_open_request_never_targets_a_stale_gui_generation(self):
        with tempfile.TemporaryDirectory() as folder:
            logs=Path(folder)
            (logs/'gui-heartbeat.json').write_text(json.dumps({'run_id':'old'}))
            self.assertFalse(request_window(logs))
            (logs/'launcher-health.json').write_text(json.dumps({'run_id':'current'}))
            self.assertTrue(request_window(logs))
            self.assertEqual('current',read_json(logs/'show-window.json')['run_id'])

    def test_live_window_without_functional_poll_cannot_mask_a_stall(self):
        watch = HeartbeatWatch("run")
        watch.observe({"run_id":"run", "updated_at":200, "poll_updated_at":100})
        self.assertFalse(watch.fresh(200))
        watch.observe({"run_id":"run", "updated_at":201, "poll_updated_at":201})
        self.assertTrue(watch.fresh(202))

    def test_transient_unreadable_file_keeps_original_expiry(self):
        watch = HeartbeatWatch("run")
        watch.observe({"run_id": "run", "updated_at": 100})
        watch.observe({})
        self.assertTrue(watch.fresh(102))
        self.assertTrue(watch.fresh(145))
        self.assertFalse(watch.fresh(146))

    def test_wrong_run_or_malformed_data_cannot_grant_freshness(self):
        watch = HeartbeatWatch("run")
        for data in (None, {"run_id": "other", "updated_at": 100}, {"run_id": "run", "updated_at": "100"}, {"run_id": "run", "updated_at": float("nan")}):
            watch.observe(data)
        self.assertFalse(watch.fresh(101))
        watch.observe({"run_id": "run", "updated_at": 100})
        watch.observe({"run_id": "run", "updated_at": 90})
        self.assertTrue(watch.fresh(145))

    def test_real_healthy_child_is_not_killed_on_one_failed_read(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "notification_watch.py").write_text('''import json,os,time
from pathlib import Path
root=Path(__file__).parent
count=root/'count.txt'
count.write_text(str(int(count.read_text())+1 if count.exists() else 1))
start=time.monotonic()
while time.monotonic()-start<.85:
    (root/'logs/gui-heartbeat.json').write_text(json.dumps({'run_id':os.environ['SENTINELLA_RUN_ID'],'updated_at':time.time()}))
    time.sleep(.015)
(root/'logs/exit-intent.json').write_text(json.dumps({'run_id':os.environ['SENTINELLA_RUN_ID']}))
''', encoding="utf-8")
            observed_at = None
            injected = False
            def intermittent_read(path):
                nonlocal observed_at, injected
                value = read_json(path)
                if path.name == "gui-heartbeat.json" and value:
                    if observed_at is None:
                        observed_at = time.monotonic()
                    if not injected and time.monotonic() - observed_at > .45:
                        injected = True
                        return {}
                return value
            with patch("sentinel_launcher.read_json", side_effect=intermittent_read):
                Launcher(root, executable=sys.executable, source_root=root,check_seconds=.02, restart_scale=.001, watchdog_grace=.4, heartbeat_timeout=.25).run()
            self.assertTrue(injected)
            self.assertEqual((root / "count.txt").read_text(), "1")
            records = [json.loads(line) for line in (root / "logs/launcher.jsonl").read_text().splitlines()]
            self.assertFalse(any(item["event"] == "unresponsive" for item in records))


if __name__ == "__main__":
    unittest.main()
