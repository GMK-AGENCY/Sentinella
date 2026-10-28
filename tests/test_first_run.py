import json
from pathlib import Path
import tempfile
import unittest

from config_store import ensure_local_files


class FirstRunTests(unittest.TestCase):
    def test_clean_clone_starts_paused_without_permissions_or_messages(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            ensure_local_files(root)
            config = json.loads((root / "approval-config.json").read_text(encoding="utf-8"))
            self.assertTrue(config["paused"])
            self.assertFalse(config["enabled"])
            self.assertFalse(config["away_mode"])
            self.assertFalse(config["start_with_windows"])
            self.assertEqual(config["allowed_project_roots"], [])
            self.assertEqual(json.loads((root / "messages-config.json").read_text()), {"jobs": []})
            self.assertTrue(all(not a["questions_enabled"] and not a["approvals_enabled"] for a in config["apps"].values()))

    def test_first_run_never_replaces_saved_or_even_invalid_user_data(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "approval-config.json").write_bytes(b"saved user configuration")
            (root / "messages-config.json").write_bytes(b"private message and schedule")
            ensure_local_files(root)
            self.assertEqual((root / "approval-config.json").read_bytes(), b"saved user configuration")
            self.assertEqual((root / "messages-config.json").read_bytes(), b"private message and schedule")
