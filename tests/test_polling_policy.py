import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from polling_policy import ScanClock, interval, validate_interval
from notification_backend import wait_for_poll
from question_supervisor import scheduled_scan, process_target
from settings_panel import save_settings


class PollingTests(unittest.TestCase):
    def test_defaults_and_invalid_intervals(self):
        self.assertEqual(interval({}, "notifications"), 30)
        self.assertEqual(interval({}, "chats"), 900)
        for value in ("nan", "inf", "oops", -1, 0):
            with self.assertRaises(ValueError):
                validate_interval("notifications", value)
            self.assertEqual(interval({"notification_poll_seconds": value}, "notifications"), 30)
        self.assertEqual(validate_interval("notifications", "12,5"), 12.5)

    def test_both_apps_scan_once_per_fifteen_minutes(self):
        config = {"idle_seconds_before_actions": 0, "apps": {"codex": {}, "claude": {}}}
        scans = ScanClock()
        with patch("question_supervisor.run_adapter", return_value={}) as adapter, patch("question_supervisor.log"):
            for now in (0, 2, 30, 300, 899, 900, 901):
                for app in config["apps"]:
                    scheduled_scan(config, app, scans, now)
        self.assertEqual(adapter.call_count, 4)
        self.assertEqual([c.args[0] for c in adapter.call_args_list], ["codex", "claude", "codex", "claude"])

    def test_shortening_interval_applies_to_existing_deadline(self):
        scans = ScanClock()
        scans.mark("codex", 100)
        self.assertFalse(scans.due("codex", 160, 900))
        self.assertTrue(scans.due("codex", 160, 60))
        self.assertEqual(scans.remaining("codex", 160, 900), 840)

    def test_pause_neither_scans_nor_consumes_deadline(self):
        scans = ScanClock()
        with patch("question_supervisor.run_adapter") as adapter, patch("question_supervisor.bounded_worker") as worker:
            self.assertIsNone(scheduled_scan({"paused": True}, "codex", scans, 100))
        adapter.assert_not_called()
        worker.assert_not_called()
        self.assertTrue(scans.due("codex", 100, 900))

    def test_file_approval_sweep_follows_chat_interval_too(self):
        config = {"enabled": True, "idle_seconds_before_actions": 0, "apps": {"codex": {"approvals_enabled": True}}}
        scans = ScanClock()
        with patch("question_supervisor.run_adapter", return_value={}), patch("question_supervisor.bounded_worker", return_value={}) as worker, patch("question_supervisor.log"):
            for now in (0, 2, 30, 899, 900):
                scheduled_scan(config, "codex", scans, now)
        self.assertEqual(worker.call_count, 2)

    def test_reported_notification_does_not_wait_for_next_sweep(self):
        config = {"away_mode": True, "apps": {"codex": {"questions_enabled": True}}}
        scans = ScanClock()
        scans.mark("codex", 0)
        self.assertFalse(scans.due("codex", 30, 900))
        with patch("question_supervisor.run_adapter", return_value={"action": "selected_chat"}) as adapter:
            result = process_target(config, "codex", "Blocco rilevato")
        self.assertEqual(result["action"], "selected_chat")
        adapter.assert_called_once_with("codex", "Blocco rilevato", act=True)

    def test_notification_wait_has_heartbeats_without_fast_rescanning(self):
        clock = [0]
        sleeps = []

        async def sleep(seconds):
            sleeps.append(seconds)
            clock[0] += seconds

        with patch("notification_backend.emit") as emit:
            asyncio.run(wait_for_poll(0, read_config=lambda: {}, clock=lambda: clock[0], sleep=sleep))
        self.assertEqual(clock[0], 30)
        self.assertTrue(all(seconds <= 5 for seconds in sleeps))
        self.assertEqual(emit.call_count, 6)
        self.assertTrue(all(c.kwargs["type"] == "heartbeat" for c in emit.call_args_list))

    def test_notification_wait_reloads_changed_interval(self):
        clock = [0]

        async def sleep(seconds):
            clock[0] += seconds

        with patch("notification_backend.emit"):
            asyncio.run(wait_for_poll(0, read_config=lambda: {"notification_poll_seconds": 30 if clock[0] < 5 else 10}, clock=lambda: clock[0], sleep=sleep))
        self.assertEqual(clock[0], 10)

    def test_settings_preserve_pause_and_delegated_roots(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "approval-config.json"
            original = {"paused": True, "allowed_project_roots": ["C:/project"], "apps": {"codex": {"questions_enabled": True}}}
            path.write_text(json.dumps(original), encoding="utf-8")
            with patch("settings_panel.ROOT", root), patch("settings_panel.load_config", side_effect=lambda: json.loads(path.read_text())):
                saved = save_settings("45", "2,5", True, "1,5")
                self.assertEqual(saved["idle_seconds_before_actions"], 90)
                from automation_policy import action_block_reason
                running = {**saved, "paused": False}
                self.assertIn("90 secondi", action_block_reason(running, observed_idle=89.9))
                self.assertIsNone(action_block_reason(running, observed_idle=90))
                self.assertEqual(saved["notification_poll_seconds"], 45)
                self.assertEqual(saved["chat_scan_seconds"], 150)
                self.assertTrue(saved["paused"])
                self.assertEqual(saved["allowed_project_roots"], original["allowed_project_roots"])
                self.assertEqual(saved["apps"], original["apps"])
                before = path.read_text()
                with self.assertRaises(ValueError):
                    save_settings(30, "nan", True)
                self.assertEqual(path.read_text(), before)
                for value in ("nan", "inf", "-1", "1441", "no"):
                    with self.assertRaises(ValueError):
                        save_settings(30, 5, True, value)
                    self.assertEqual(path.read_text(), before)
                saved = save_settings(30, 5, True, 0)
                self.assertEqual(saved["idle_seconds_before_actions"], 0)


if __name__ == "__main__":
    unittest.main()
