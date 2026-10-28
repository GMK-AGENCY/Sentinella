from pathlib import Path
import tempfile
import unittest

from codex_permission_selftest import approval_response


class PermissionSelftestGuards(unittest.TestCase):
    def test_exact_native_request_only_and_never_a_repeat_or_broader_grant(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "proof.txt"
            request = {"method": "item/fileChange/requestApproval", "params": {
                "threadId": "test-thread", "turnId": "test-turn", "itemId": "test-item"}}
            changes = {"test-item": [{"path": str(target), "kind": {"type": "add"}}]}
            def decision(already=False):
                return approval_response(request, changes, target, "test-thread", "test-turn", already)[0]["decision"]
            self.assertEqual(decision(), "accept")
            self.assertEqual(decision(True), "cancel")
            request["params"]["threadId"] = "another-thread"
            self.assertEqual(decision(), "cancel")
            request["params"]["threadId"] = "test-thread"
            request["params"]["grantRoot"] = str(target.parent.parent)
            self.assertEqual(decision(), "cancel")
            del request["params"]["grantRoot"]
            changes["test-item"][0]["path"] = str(target.parent / "other.txt")
            self.assertEqual(decision(), "cancel")
            changes["test-item"][0]["path"] = str(target)
            changes["test-item"][0]["kind"] = {"type": "delete"}
            self.assertEqual(decision(), "cancel")
            changes["test-item"][0]["kind"] = {"type": "add", "movePath": str(target.parent / "other.txt")}
            self.assertEqual(decision(), "cancel")
