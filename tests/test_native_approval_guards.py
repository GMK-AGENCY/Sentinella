from dataclasses import dataclass
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from approval_desktop import inspect_dialog, codex_resource_path, verified_hover_point


@dataclass
class Node:
    Name: str
    ControlTypeName: str
    IsEnabled: bool = True
    IsOffscreen: bool = False
    AriaRole: str = ""
    HelpText: str = ""
    Element: object = None
    parent: object = None

    def GetParentControl(self):
        return self.parent


class NativeApprovalGuardTests(unittest.TestCase):
    def test_wrapped_resource_hover_uses_a_verified_text_segment(self):
        target = Node("wrapped-name", "TextControl")
        target.GetRuntimeId = lambda: [42, 1]
        target.BoundingRectangle = SimpleNamespace(left=-1100, right=-400, top=1000, bottom=1042)
        unrelated = Node("", "GroupControl")
        unrelated.GetRuntimeId = lambda: [42, 99]
        visited = []
        def hit(x, y):
            visited.append((x, y))
            return target if (x, y) == (-402, 1002) else unrelated
        self.assertEqual(verified_hover_point(target, hit), (-402, 1002))
        self.assertLessEqual(len(visited), 9)
        self.assertIsNone(verified_hover_point(target, lambda x, y: unrelated))

    def test_identical_text_in_a_new_native_request_is_not_a_duplicate(self):
        dialog = Node("Consenso file", "WindowControl")
        runtime = [1]
        dialog.GetRuntimeId = lambda: runtime
        nodes = [dialog, Node("write file\nC:/delegated/file.txt", "TextControl"),
                 Node("Consenti una volta", "ButtonControl"), Node("Nega", "ButtonControl")]
        with patch("approval_desktop.walk", return_value=nodes):
            first = inspect_dialog(dialog, ["C:/delegated"], time.monotonic()+2)["fingerprint"]
            self.assertEqual(first, inspect_dialog(dialog, ["C:/delegated"], time.monotonic()+2)["fingerprint"])
            runtime = [2]
            self.assertNotEqual(first, inspect_dialog(dialog, ["C:/delegated"], time.monotonic()+2)["fingerprint"])

    def test_codex_inline_form_does_not_trust_a_path_in_the_models_reason(self):
        dialog = Node("", "GroupControl")
        target = Node("file.txt", "TextControl")
        nodes = [dialog, Node("Autorizzazioni", "TextControl"),
                 Node("Consentire a ChatGPT di modificare i contenuti di", "TextControl"),
                 target, Node("?", "TextControl"),
                 Node("write file C:/delegated/file.txt", "TextControl"),
                 Node("", "GroupControl", AriaRole="form"),
                 Node("Consenti una volta", "ButtonControl"), Node("Rifiuta", "ButtonControl")]
        with patch("approval_desktop.walk", return_value=nodes):
            result = inspect_dialog(dialog, ["C:/delegated"], time.monotonic()+2)
            self.assertFalse(result["summary"]["eligible"])
            self.assertIn("percorso completo", result["summary"]["reason"])
            target.HelpText = "C:/outside/file.txt"
            self.assertFalse(inspect_dialog(dialog, ["C:/delegated"], time.monotonic()+2)["summary"]["eligible"])
            target.HelpText = "C:/delegated/another.txt"
            self.assertIsNone(codex_resource_path(target))
            target.HelpText = "C:/delegated/file.txt"
            observed = inspect_dialog(dialog, ["C:/delegated"], time.monotonic()+2)
            self.assertTrue(observed["summary"]["eligible"])
            self.assertEqual(observed["summary"]["paths"], [target.HelpText])
            reason = nodes[5]
            for operation in ["delete", "powershell", "shell", "cmd.exe", "network"]:
                reason.Name = operation + " C:/delegated/file.txt"
                self.assertFalse(inspect_dialog(dialog, ["C:/delegated"], time.monotonic()+2)["summary"]["eligible"])
            reason.Name = "write file C:/delegated/file.txt"
            target.HelpText = ""
            # Reproduce Codex's real resource wrapper: the path belongs to its
            # native FullDescription two levels above the basename text.
            resource = Node("", "GroupControl", Element=SimpleNamespace(
                GetCurrentPropertyValue=lambda property_id: "C:/delegated/file.txt" if property_id == 30159 else None))
            target.parent = Node("", "GroupControl", parent=resource)
            self.assertEqual(codex_resource_path(target), "C:/delegated/file.txt")
            self.assertTrue(inspect_dialog(dialog, ["C:/delegated"], time.monotonic()+2)["summary"]["eligible"])
            self.assertFalse(inspect_dialog(dialog, ["C:/outside"], time.monotonic()+2)["summary"]["eligible"])
            target.parent = None
            target.Name = "C:/outside/file.txt"
            self.assertFalse(inspect_dialog(dialog, ["C:/delegated"], time.monotonic()+2)["summary"]["eligible"])
            target.Name = "C:/delegated/file.txt"
            self.assertTrue(inspect_dialog(dialog, ["C:/delegated"], time.monotonic()+2)["summary"]["eligible"])

    def test_persistent_consent_requires_both_explicit_scope_and_user_preference(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            dialog = Node("Consenso file", "WindowControl")
            text = Node(f"write file (questa sessione)\n{root / 'file.txt'}", "TextControl")
            nodes = [dialog, text, *[Node(label, "ButtonControl") for label in ["Consenti sempre", "Consenti una volta", "Nega"]]]
            with patch("approval_desktop.walk", return_value=nodes):
                self.assertEqual(inspect_dialog(dialog, [root], time.monotonic()+2, False)["summary"]["selected"], "Consenti una volta")
                self.assertEqual(inspect_dialog(dialog, [root], time.monotonic()+2, True)["summary"]["selected"], "Consenti sempre")
                text.Name = f"write file\n{root / 'file.txt'}"
                self.assertEqual(inspect_dialog(dialog, [root], time.monotonic()+2, True)["summary"]["selected"], "Consenti una volta")

    def test_destructive_shell_network_and_outside_paths_are_never_eligible(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            dialog = Node("Richiesta", "WindowControl")
            text = Node("", "TextControl")
            nodes = [dialog, text, Node("Consenti una volta", "ButtonControl"), Node("Nega", "ButtonControl")]
            with patch("approval_desktop.walk", return_value=nodes):
                for operation in ["delete", "powershell", "shell", "cmd.exe", "exec_command", "network", "browser", "password", "login"]:
                    text.Name = f"write file {operation}\n{root / 'file.txt'}"
                    self.assertFalse(inspect_dialog(dialog, [root], time.monotonic()+2)["summary"]["eligible"])
                text.Name = f"write file\n{root.parent / 'outside.txt'}"
                self.assertFalse(inspect_dialog(dialog, [root], time.monotonic()+2)["summary"]["eligible"])
