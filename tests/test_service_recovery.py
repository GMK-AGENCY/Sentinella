import queue
import time
import unittest
from unittest.mock import Mock, patch

from notification_watch import Monitor
from service_recovery import ServiceRecovery, supervisor_stale


class ServiceRecoveryTests(unittest.TestCase):
    def test_repeated_crashes_keep_retrying_with_capped_delay(self):
        policy = ServiceRecovery()
        now = 0
        delays = []
        for _ in range(12):
            delay = policy.failed(now)
            delays.append(delay)
            self.assertFalse(policy.due(now + delay - .1))
            self.assertTrue(policy.due(now + delay))
            now += delay
            policy.started()
            self.assertFalse(policy.due(now))
        self.assertEqual(delays[:5], [5, 10, 20, 40, 60])
        self.assertEqual(delays[-1], 60)

    def test_two_healthy_minutes_reset_failure_streak(self):
        policy = ServiceRecovery()
        policy.failed(0)
        policy.started()
        policy.healthy(10)
        policy.healthy(129)
        self.assertEqual(policy.failures, 1)
        policy.healthy(130)
        self.assertEqual(policy.failed(131), 5)

    def test_missing_or_invalid_supervisor_heartbeat_has_bounded_grace(self):
        for health in ({}, {"updated_at": "broken"}, {"updated_at": None}):
            self.assertFalse(supervisor_stale(health, 100, 189))
            self.assertTrue(supervisor_stale(health, 100, 191))
        self.assertFalse(supervisor_stale({"updated_at": "1970-01-01T00:03:00+00:00"}, 100, 200))
        self.assertTrue(supervisor_stale({"updated_at": "1970-01-01T00:00:00+00:00"}, 100, 191))

    def test_dead_supervisor_recovers_without_any_health_file(self):
        monitor = Monitor.__new__(Monitor)
        monitor.supervisor = Mock()
        monitor.supervisor.poll.return_value = 7
        monitor.recover_supervisor = Mock()
        monitor.check_supervisor({})
        monitor.recover_supervisor.assert_called_once_with("Processo interrotto")

    def test_live_but_hung_supervisor_recovers(self):
        monitor = Monitor.__new__(Monitor)
        monitor.supervisor = Mock()
        monitor.supervisor.poll.return_value = None
        monitor.supervisor_started_wall = time.time() - 100
        monitor.recover_supervisor = Mock()
        monitor.check_supervisor({})
        monitor.recover_supervisor.assert_called_once_with("Nessun aggiornamento per 90 secondi")

    def test_denied_windows_access_does_not_retry(self):
        monitor = Monitor.__new__(Monitor)
        monitor.last_health_read = time.monotonic()
        monitor.queue = queue.Queue()
        monitor.queue.put((123, {"type": "status", "state": "blocked", "message": "Denied"}))
        monitor.worker = Mock(pid=123)
        monitor.monitoring = True
        monitor.root = Mock()
        monitor.reader_recovery = ServiceRecovery()
        monitor.last_message = None
        monitor.set_status = Mock()
        monitor.start_worker = Mock()
        with patch.object(monitor, "stop_worker", side_effect=lambda: setattr(monitor, "worker", None)):
            monitor.poll()
        self.assertFalse(monitor.monitoring)
        monitor.start_worker.assert_not_called()


if __name__ == "__main__":
    unittest.main()
