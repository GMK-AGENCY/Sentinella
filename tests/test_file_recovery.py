import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import file_recovery as recovery
from settings_panel import save_shared_controls


class FileRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "history.jsonl"
        self.file = self.root / "file.txt"
        self.meta = {"app": "codex", "title": "Test", "thread_id": "session", "history": str(self.path)}
        self.config = {"enabled": True, "automatic_file_recovery": True, "idle_seconds_before_actions": 0,
                       "allowed_project_roots": [str(self.root)], "apps": {"codex": {"approvals_enabled": True}, "claude": {"approvals_enabled": True}}}
        self.add({"id": "session"}, type="session_meta")
        self.write_failure()

    def tearDown(self):
        self.temp.cleanup()

    def add(self, payload, at=110, **extra):
        record = {"timestamp": datetime.fromtimestamp(at, timezone.utc).isoformat(), "type": "response_item", "payload": payload, **extra}
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record) + "\n")

    def write_failure(self):
        self.add({"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Create file"}]}, at=100)
        self.add({"type": "custom_tool_call", "name": "exec", "call_id": "c", "input": 'text(await tools.apply_patch("*** Add File: file.txt"));'})
        self.add({"type": "custom_tool_call_output", "call_id": "c", "output": "Failed to write file " + str(self.file)})

    def test_only_actual_failed_tool_in_current_turn_is_eligible(self):
        self.assertIsNotNone(recovery.failure(self.meta, 90, 125))
        self.add({"type": "message", "role": "assistant", "content": [{"type": "text", "text": "Failed to write file fake"}]})
        self.assertEqual(recovery.failure(self.meta, 90, 125)["paths"], [str(self.file)])
        self.add({"type": "message", "role": "user", "content": [{"type": "input_text", "text": "New task"}]})
        self.assertIsNone(recovery.failure(self.meta, 90, 125))

    def test_no_historical_or_recent_or_recovery_loop(self):
        self.assertIsNone(recovery.failure(self.meta, 115, 125))
        self.assertIsNone(recovery.failure(self.meta, 90, 115))
        self.add({"type": "message", "role": "user", "content": [{"type": "input_text", "text": recovery.MARKER + "already requested]"}]}, 120)
        self.add({"type": "custom_tool_call", "name": "apply_patch", "call_id": "b"}, 121)
        self.add({"type": "custom_tool_call_output", "call_id": "b", "output": "Failed to write file " + str(self.file)}, 122)
        self.assertIsNone(recovery.failure(self.meta, 90, 140))

    def test_later_success_means_no_recovery_needed(self):
        self.add({"type": "custom_tool_call_output", "call_id": "ordinary", "output": 'Exit code: 0\nfile read back'})
        self.assertIsNone(recovery.failure(self.meta, 90, 140))

    def test_successful_patch_retry_needs_no_recovery(self):
        self.add({"type": "custom_tool_call", "name": "apply_patch", "call_id": "ok"})
        self.add({"type": "custom_tool_call_output", "call_id": "ok", "output": "{}"})
        self.assertIsNone(recovery.failure(self.meta, 90, 140))

    def test_failure_while_awaiting_idle_is_kept_from_enable_time(self):
        self.config["file_recovery_enabled_at"] = 95
        adapter = Mock(return_value={"action": "drafted"})
        result = recovery.tick(lambda: self.config, lambda *_: [self.meta], adapter, 125, self.root)
        self.assertEqual(result["action"], "drafted")

    def test_changed_task_cancels_reserved_recovery_without_touching_draft(self):
        adapter = Mock(return_value={"action": "drafted"})
        run = lambda now: recovery.tick(lambda: self.config, lambda *_: [self.meta], adapter, now, self.root)
        run(90)
        self.assertEqual(run(125)["action"], "drafted")
        self.add({"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Stop this task"}]}, 126)
        adapter.reset_mock()
        self.assertEqual(run(127)["action"], "resolved_or_changed")
        adapter.assert_not_called()
        self.assertEqual(run(128)["action"], "idle")

    def test_receipt_from_replaced_history_is_rejected(self):
        self.path.write_text(json.dumps({"type": "session_meta", "payload": {"id": "wrong"}}) + "\n")
        with self.assertRaises(ValueError):
            recovery.receipt(self.meta, "message", 100)

    def test_deletions_are_never_recovered(self):
        self.path.write_text("")
        self.add({"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Delete"}]}, at=100)
        self.add({"type": "custom_tool_call", "name": "apply_patch", "call_id": "delete", "input": "*** Delete File: file.txt"})
        self.add({"type": "custom_tool_call_output", "call_id": "delete", "output": "Failed to write file " + str(self.file)})
        self.assertIsNone(recovery.failure(self.meta, 90, 125))

    def test_scope_and_pause_and_no_draft_overwrite(self):
        meta = {**self.meta, "paths": [str(self.file)]}
        self.assertTrue(recovery.allowed(self.config, meta))
        self.assertFalse(recovery.allowed(self.config, {**meta, "paths": [str(self.root.parent / "outside")]}))
        self.config["paused"] = True
        adapter = Mock()
        self.assertEqual(recovery.tick(lambda: self.config, adapter=adapter, root=self.root)["action"], "paused")
        adapter.assert_not_called()

    def test_real_receipt_and_crash_never_resend(self):
        sources = lambda *_: [self.meta]
        adapter = Mock(return_value={"action": "drafted"})
        run = lambda now: recovery.tick(lambda: self.config, sources, adapter, now, self.root)
        self.assertEqual(run(90)["action"], "idle")  # Persist install boundary.
        self.assertEqual(run(125)["action"], "drafted")
        message = adapter.call_args.args[1]["message"]
        button = Mock()
        button.GetInvokePattern.return_value.Invoke.side_effect = RuntimeError("unknown result")
        adapter.return_value = {"action": "ready", "button": button}
        with self.assertRaises(RuntimeError):
            run(126)
        adapter.reset_mock()
        self.assertEqual(run(127)["action"], "awaiting_receipt")
        adapter.assert_not_called()
        self.add({"type": "message", "role": "assistant", "content": [{"type": "text", "text": message}]}, 128)
        self.assertEqual(run(129)["action"], "awaiting_receipt")
        self.add({"type": "message", "role": "user", "content": [{"type": "input_text", "text": message + "\n"}]}, 130)
        self.assertEqual(run(131)["action"], "confirmed")
        self.assertEqual(run(132)["action"], "idle")

    def test_claude_requires_real_write_tool_error_and_session(self):
        self.path.write_text("")
        self.meta["app"] = "claude"
        def line(kind, content):
            self.add({}, sessionId="session", uuid="user-1", type=kind, message={"content": content})
        line("user", "Create file")
        line("assistant", [{"type": "tool_use", "name": "Write", "id": "w", "input": {"file_path": str(self.file)}}])
        line("user", [{"type": "tool_result", "tool_use_id": "w", "is_error": True, "content": "Error writing file " + str(self.file)}])
        self.assertEqual(recovery.failure(self.meta, 90, 125)["paths"], [str(self.file)])
        self.assertIsNone(recovery.failure({**self.meta, "thread_id": "other"}, 90, 125))

    def test_common_controls_preserve_pause_roots_and_timer_config(self):
        config = {**copy.deepcopy(self.config), "paused": True, "unrelated": "keep"}
        with patch("settings_panel.load_config", return_value=config), patch("settings_panel.ROOT", self.root):
            result = save_shared_controls(True, False, True)
        self.assertTrue(result["paused"])
        self.assertEqual(result["unrelated"], "keep")
        for app in ("codex", "claude"):
            self.assertTrue(result["apps"][app]["questions_enabled"])
            self.assertFalse(result["apps"][app]["approvals_enabled"])
        self.assertFalse((self.root / "messages-config.json").exists())


if __name__ == "__main__":
    unittest.main()
