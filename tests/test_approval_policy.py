import tempfile
from pathlib import Path
import unittest

from approval_policy import file_change_decision, is_inside, pick_button


class ApprovalPolicyTests(unittest.TestCase):
    def test_claude_native_session_scope_label(self):
        label = "Consenti sempre , Si applica solo a questa sessione"
        self.assertEqual(pick_button(["Nega", label, "Consenti una volta"]), label)
        self.assertEqual(pick_button(["Nega", "Consenti una volta"]), "Consenti una volta")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_prefers_always_and_falls_back_to_once(self):
        self.assertEqual(pick_button(["Annulla", "Consenti una volta", "Consenti sempre"]), "Consenti sempre")
        self.assertEqual(pick_button(["Annulla", "Consenti"]), "Consenti")
        self.assertEqual(pick_button(["Allow once", "Always allow"]), "Always allow")

    def test_once_when_persistent_scope_is_not_delegated(self):
        self.assertEqual(pick_button(["Consenti sempre", "Consenti una volta"], False), "Consenti una volta")

    def test_accepts_local_file_creation(self):
        change = {"path": str(self.root / "nuovo.txt"), "kind": {"type": "add"}}
        self.assertEqual(file_change_decision([change], [self.root]).action, "acceptForSession")

    def test_refuses_path_traversal_and_prefix_sibling(self):
        self.assertFalse(is_inside(str(self.root / ".." / "outside.txt"), [self.root]))
        self.assertFalse(is_inside(str(self.root) + "-other/file.txt", [self.root]))

    def test_missing_paths_and_deletion_are_manual(self):
        self.assertEqual(file_change_decision([], [self.root]).action, "manual")
        self.assertEqual(file_change_decision([{"path": str(self.root / "old.txt"), "kind": {"type": "delete"}}], [self.root]).action, "manual")

    def test_broader_persistent_grant_is_manual(self):
        change = {"path": str(self.root / "nuovo.txt"), "kind": "add"}
        self.assertEqual(file_change_decision([change], [self.root], grant_root=str(self.root.parent)).action, "manual")


if __name__ == "__main__":
    unittest.main()
