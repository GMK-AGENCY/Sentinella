import ctypes
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from notification_watch import Monitor
from question_supervisor import publish
from sentinel_launcher import atomic_json


class HealthIoTests(unittest.TestCase):
    def test_reader_diagnostic_io_error_does_not_abort_gui_poll(self):
        monitor = Monitor.__new__(Monitor)
        monitor.current_state = "active"
        monitor.last_scan = "observed"
        monitor.rows = {}
        monitor.worker = None
        monitor.restarts = 0
        monitor.log = Mock()
        with patch("sentinel_launcher.atomic_json", side_effect=[OSError(22, "Invalid argument"), None]):
            self.assertFalse(monitor.write_health())
            self.assertTrue(monitor.write_health())

    def test_failed_poll_is_rescheduled_and_next_poll_restores_functional_heartbeat(self):
        monitor = Monitor.__new__(Monitor)
        monitor.root = Mock()
        monitor.log = Mock()
        monitor.last_poll_success = 10
        monitor.poll_once = Mock(side_effect=[OSError(22, "Invalid argument"), None])
        with patch("notification_watch.time.time", return_value=20):
            monitor.poll()
            self.assertEqual(monitor.last_poll_success, 10)
            monitor.poll()
            self.assertEqual(monitor.last_poll_success, 20)
        self.assertEqual(monitor.root.after.call_count, 2)

    @unittest.skipUnless(os.name == "nt", "Windows file sharing regression")
    def test_real_windows_reader_lock_is_retried_then_replaced(self):
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "health.json"
            atomic_json(path, {"old": True})
            handle = kernel.CreateFileW(str(path), 0x80000000, 3, None, 3, 0x80, None)
            self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
            try:
                # Prove that this is an actual replacement-blocking lock.
                with patch("sentinel_launcher.time.sleep"):
                    with self.assertRaises(PermissionError):
                        atomic_json(path, {"new": True})
                self.assertEqual(json.loads(path.read_text()), {"old": True})
                release = threading.Timer(.025, lambda held=handle: kernel.CloseHandle(held))
                release.start()
                handle = None
                try:
                    atomic_json(path, {"new": True})
                finally:
                    release.join(timeout=1)
                self.assertEqual(json.loads(path.read_text()), {"new": True})
            finally:
                if handle is not None:
                    kernel.CloseHandle(handle)

    def test_coordinator_survives_health_write_failure_then_publishes(self):
        with patch("sentinel_launcher.atomic_json", side_effect=[PermissionError("locked"), None]), patch("question_supervisor.log"):
            self.assertFalse(publish({"updated_at": "first"}))
            self.assertTrue(publish({"updated_at": "next"}))

    def test_gui_keeps_scheduling_heartbeat_after_write_failure(self):
        monitor = Monitor.__new__(Monitor)
        monitor.root = Mock()
        monitor.run_id = "fixture"
        monitor.log = Mock()
        with patch("sentinel_launcher.atomic_json", side_effect=PermissionError("locked")):
            monitor.gui_heartbeat()
        monitor.root.after.assert_called_once_with(5000, monitor.gui_heartbeat)
        monitor.log.assert_called_once()


if __name__ == "__main__":
    unittest.main()
