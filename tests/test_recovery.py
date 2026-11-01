import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from message_templates import AWAY_REPLY, OLD_AWAY_REPLY, reply_is_replaceable
from polling_policy import ScanClock
from question_supervisor import scheduled_scan
from startup_manager import set_startup


class RecoveryTests(unittest.TestCase):
    def test_reply_defers_until_return_not_a_calendar_date(self):
        self.assertIn("per quando rientra e ti scrive", AWAY_REPLY)
        self.assertNotIn("domani", AWAY_REPLY)
        config = {"away_reply": AWAY_REPLY, "legacy_away_replies": [OLD_AWAY_REPLY]}
        self.assertTrue(reply_is_replaceable(OLD_AWAY_REPLY, config))
        self.assertFalse(reply_is_replaceable("La mia risposta in bozza", config))

    def test_late_app_is_rechecked_every_thirty_seconds(self):
        config = {"idle_seconds_before_actions": 0, "apps": {"codex": {}}, "chat_scan_seconds": 900}
        clock = ScanClock()
        with patch("question_supervisor.run_adapter", side_effect=[{"windows": 0}, {"windows": 0}, {"windows": 1}]) as adapter, patch("question_supervisor.log"):
            self.assertFalse(scheduled_scan(config, "codex", clock, 0)["ready"])
            self.assertIsNone(scheduled_scan(config, "codex", clock, 29))
            self.assertFalse(scheduled_scan(config, "codex", clock, 30)["ready"])
            self.assertTrue(scheduled_scan(config, "codex", clock, 60)["ready"])
            self.assertIsNone(scheduled_scan(config, "codex", clock, 90))
        self.assertEqual(adapter.call_count, 3)
        self.assertEqual(clock.remaining("codex", 60, 900), 900)

    def test_empty_accessibility_tree_is_waiting_not_a_successful_scan(self):
        config = {"idle_seconds_before_actions": 0, "apps": {"codex": {}}, "chat_scan_seconds": 900}
        clock = ScanClock()
        with patch("question_supervisor.run_adapter", return_value={"windows": 1, "available": False}), patch("question_supervisor.log"):
            self.assertFalse(scheduled_scan(config, "codex", clock, 0)["ready"])
            self.assertIsNone(scheduled_scan(config, "codex", clock, 29))
            self.assertFalse(scheduled_scan(config, "codex", clock, 30)["ready"])
        self.assertEqual(clock.remaining("codex", 30, 900), 30)

    def test_startup_switch_writes_only_its_shortcut_and_preserves_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = {"paused": True, "allowed_project_roots": ["C:/project"]}
            target = root / "Sentinella.lnk"
            client = Mock()
            shortcut = client.CreateShortcut.return_value
            shortcut.Save.side_effect = lambda: target.write_text("fixture")
            with patch("startup_manager.ROOT", root), patch("startup_manager.load_config", return_value=config):
                set_startup(True, target, client)
                self.assertTrue(target.exists())
                self.assertIn("sentinel_launcher.py", shortcut.Arguments)
                self.assertIn("--background", shortcut.Arguments)
                from app_icon import shortcut_icon
                self.assertEqual(shortcut.IconLocation,shortcut_icon())
                saved = json.loads((root / "approval-config.json").read_text())
                self.assertTrue(saved["paused"])
                self.assertEqual(saved["allowed_project_roots"], ["C:/project"])
                set_startup(False, target, client)
                self.assertFalse(target.exists())

    def test_real_child_crash_restarts_then_explicit_close_stops(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "state.json").write_text(json.dumps({"paused": True, "message": "saved", "interval": 900}))
            (root / "notification_watch.py").write_text('''import json, os
from pathlib import Path
root=Path(__file__).parent
counter=root/'counter.txt'
count=int(counter.read_text())+1 if counter.exists() else 1
counter.write_text(str(count))
if count==1:
    os._exit(7)
assert json.loads((root/'state.json').read_text())['message']=='saved'
(root/'logs/exit-intent.json').write_text(json.dumps({'run_id':os.environ['SENTINELLA_RUN_ID']}))
''', encoding="utf-8")
            code = "from sentinel_launcher import Launcher;import sys;Launcher(root=sys.argv[1],source_root=sys.argv[1],check_seconds=.02,restart_scale=.001).run()"
            result = subprocess.run([sys.executable, "-c", code, str(root)], capture_output=True, text=True, timeout=8, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((root / "counter.txt").read_text(), "2")
            records = [json.loads(line) for line in (root / "logs/launcher.jsonl").read_text().splitlines()]
            self.assertEqual(sum(row["event"] == "restart_scheduled" for row in records), 1)
            self.assertEqual(records[-1]["event"], "closed_by_user")


if __name__ == "__main__":
    unittest.main()
