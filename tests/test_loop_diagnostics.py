from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from loop_diagnostics import LoopDiagnostics


class LoopDiagnosticsTests(unittest.TestCase):
    def test_healthy_heartbeats_only_rearm_deadline_and_close_cancels(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "stall.log"
            with patch("loop_diagnostics.faulthandler.dump_traceback_later") as arm, patch("loop_diagnostics.faulthandler.cancel_dump_traceback_later") as cancel:
                diag = LoopDiagnostics(path)
                diag.heartbeat()
                diag.heartbeat()
                self.assertEqual(arm.call_count, 2)
                diag.close()
                cancel.assert_called_once()
            self.assertEqual(path.read_text(), "")

    def test_unwritable_diagnostic_path_does_not_block_app(self):
        with tempfile.TemporaryDirectory() as folder:
            diag = LoopDiagnostics(Path(folder) / "missing" / "stall.log")
            diag.heartbeat()
            diag.callback_error(ValueError, ValueError("fixture"), None)
            diag.close()
            self.assertIsNone(diag.stream)

    def test_real_stall_records_stack_without_stopping_process(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "stall.log"
            script = "from pathlib import Path;import sys,time;from loop_diagnostics import LoopDiagnostics;d=LoopDiagnostics(Path(sys.argv[1]),timeout=.04);d.heartbeat();time.sleep(.12);d.close();print('continued')"
            result = subprocess.run([sys.executable, "-c", script, str(path)], capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("continued", result.stdout)
            self.assertIn("Timeout", path.read_text())
            self.assertIn("<module>", path.read_text())


if __name__ == "__main__":
    unittest.main()
