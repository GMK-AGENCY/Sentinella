import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from question_supervisor import run


class SupervisorEventTests(unittest.TestCase):
    def test_new_notification_after_startup_is_still_queued_during_pause(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            logs = root / "logs"
            logs.mkdir()
            (root / "approval-config.json").write_text(json.dumps({"away_mode": True, "paused": True, "apps": {"codex": {"questions_enabled": True}}}))
            events = logs / "events.jsonl"
            events.write_text("")
            observed = []
            class StopTest(Exception):
                pass
            def publish(state):
                observed.append(state)
                if len(observed) == 1:
                    event = {"type": "notification", "app_id": "OpenAI.Codex_example!App", "title": "New pending chat", "body": "has a question"}
                    events.write_text(json.dumps(event) + "\n")
                else:
                    raise StopTest()
            with patch("question_supervisor.ROOT", root), patch("question_supervisor.LOGS", logs), patch("question_supervisor.publish", side_effect=publish), patch("question_supervisor.time.sleep"), patch("question_supervisor.bounded_worker") as worker:
                with self.assertRaises(StopTest):
                    run()
                worker.assert_not_called()
            self.assertEqual([state["queued"] for state in observed], [0, 1])

    def test_restart_does_not_replay_old_notifications_or_act_while_paused(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            logs = root / "logs"
            logs.mkdir()
            config = {"away_mode": True, "paused": True, "apps": {"codex": {"questions_enabled": True, "approvals_enabled": False}}}
            (root / "approval-config.json").write_text(json.dumps(config))
            old = {"type": "notification", "app_id": "OpenAI.Codex_example!App", "title": "Old completed chat", "body": "has a question"}
            (logs / "events.jsonl").write_text(json.dumps(old) + "\n")
            observed = []
            class StopTest(Exception):
                pass
            def publish(state):
                observed.append(state)
                raise StopTest()
            with patch("question_supervisor.ROOT", root), patch("question_supervisor.LOGS", logs), patch("question_supervisor.publish", side_effect=publish), patch("question_supervisor.bounded_worker") as worker:
                with self.assertRaises(StopTest):
                    run()
                worker.assert_not_called()
            self.assertEqual(observed[0]["queued"], 0)
            self.assertTrue(observed[0]["paused"])
