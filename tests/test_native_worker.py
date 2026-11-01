import json
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest
import copy
from unittest.mock import patch, Mock

import psutil

from bounded_process import run_bounded, stop_owned
from desktop_identity import executable_matches
from question_supervisor import bounded_worker, scheduled_scan
from polling_policy import ScanClock
import question_supervisor


class NativeWorkerTests(unittest.TestCase):
    def test_denied_exit_observation_is_reported_without_retry_or_escalation(self):
        process=Mock(pid=123);parent=Mock(pid=123)
        parent.is_running.return_value=False
        with patch('bounded_process.psutil.wait_procs',side_effect=psutil.AccessDenied(123)) as wait, \
             patch('bounded_process.psutil.Process',side_effect=AssertionError('No alternate process lookup')), \
             self.assertLogs('bounded_process',level='WARNING') as logs:
            self.assertFalse(stop_owned(process,{123:parent}))
        wait.assert_called_once();parent.kill.assert_called_once();process.wait.assert_called_once_with(timeout=1)
        self.assertIn('non verificabile',logs.output[0])

    def test_cleanup_observation_failure_does_not_erase_real_adapter_output(self):
        with patch('bounded_process.psutil.wait_procs',side_effect=psutil.AccessDenied(123)), \
             self.assertLogs('bounded_process',level='WARNING'):
            result=run_bounded([sys.executable,'-c',"print('completed')"],timeout=2)
        self.assertEqual(0,result['returncode']);self.assertEqual('completed',result['stdout'].strip())
        self.assertFalse(result['cleanup_verified']);self.assertFalse(result['timed_out'])

    def test_passively_seen_permission_runs_as_soon_as_pc_is_idle(self):
        config = {"paused": False, "enabled": True, "away_mode": False, "idle_seconds_before_actions": 10,
                  "chat_scan_seconds": 900, "apps": {"codex": {"questions_enabled": False,
                      "approvals_enabled": True, "approval_target_titles": ["Test"]}}}
        idle = [0]
        states = []
        action_attempts = []
        class Finished(Exception):
            pass
        def worker(script, *args):
            if script == "chat_desktop.py":
                return {"action": "none", "current_title": "Test"}
            if script == "approval_desktop.py":
                if "--inspect-only" in args:
                    return {"windows": 1, "dialogs": [{"eligible": False, "waiting_for_resource": True}], "invoked": False}
                action_attempts.append(True)
                if len(action_attempts) == 1:
                    return {"windows": 1, "dialogs": [{"eligible": True}], "invoked": False}
                return {"windows": 1, "invoked": True}
            raise AssertionError(script)
        def publish(state):
            states.append(copy.deepcopy(state))
            if state.get("last_result", {}).get("action") == "approved_file":
                raise Finished()
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            (root / "approval-config.json").write_text(json.dumps(config))
            with patch.object(question_supervisor, "ROOT", root), patch.object(question_supervisor, "LOGS", root / "logs"), \
                 patch("automation_policy.idle_seconds", side_effect=lambda: idle[0]), \
                 patch.object(question_supervisor, "load_jobs", return_value=[]), \
                 patch.object(question_supervisor, "run_adapter", return_value={"windows": 1, "current_title": "Test"}) as adapter, \
                 patch.object(question_supervisor, "bounded_worker", side_effect=worker), \
                 patch.object(question_supervisor, "publish", side_effect=publish), \
                 patch.object(question_supervisor, "log"), \
                 patch("question_supervisor.time.sleep", side_effect=lambda seconds: idle.__setitem__(0, 20)):
                with self.assertRaises(Finished):
                    question_supervisor.run()
                adapter.assert_called_once()  # No second 15-minute sweep was needed.
        self.assertEqual(states[0]["queued"], 1)
        self.assertEqual(states[0]["scan_mode"], "read_only")
        self.assertEqual(states[-1]["last_result"]["action"], "approved_file")
        self.assertEqual(states[-1]["queued"], 0)
        self.assertEqual(len(action_attempts), 2)

    def test_native_approval_error_is_preserved_for_diagnosis(self):
        config = {"enabled": True, "apps": {"codex": {"approvals_enabled": True}}}
        with patch("question_supervisor.bounded_worker", side_effect=[
                {"current_title": "Test"}, {"error": "native read timeout"}]):
            result = question_supervisor.process_target(config, "codex", "Test")
        self.assertEqual(result["error"], "native read timeout")
        self.assertEqual(result["action"], "none")

    def test_transient_layout_error_retries_with_fresh_controls(self):
        config = {"enabled": True, "apps": {"codex": {"approvals_enabled": True}}}
        for message in ["Pulsante coperto o ricreato dall'app", "La richiesta è cambiata dopo l'attivazione"]:
            with patch("question_supervisor.bounded_worker", side_effect=[
                    {"current_title": "Test"}, {"error": "RuntimeError: " + message}]):
                result = question_supervisor.process_target(config, "codex", "Test")
            self.assertTrue(result["retryable"])
            self.assertEqual(result["action"], "waiting_file_approval")

    def test_outside_delegation_requires_manual_action_without_fast_retry(self):
        config = {"enabled": True, "apps": {"codex": {"approvals_enabled": True}}}
        with patch("question_supervisor.bounded_worker", side_effect=[
                {"current_title": "Test"}, {"dialogs": [{"manual_required": True}]}]):
            result = question_supervisor.process_target(config, "codex", "Test")
        self.assertEqual(result["action"], "manual_required")

    def test_success_follows_a_permission_chain_then_returns_to_normal_interval(self):
        config = {"enabled": True, "idle_seconds_before_actions": 0,
                  "apps": {"codex": {"approvals_enabled": True}}, "chat_scan_seconds": 900}
        clock = ScanClock()
        with patch("question_supervisor.run_adapter", return_value={"windows": 1}), \
             patch("question_supervisor.bounded_worker", side_effect=[{"invoked": True}, {"invoked": False}]), \
             patch("question_supervisor.log"):
            scheduled_scan(config, "codex", clock, 0)
            self.assertTrue(clock.due("codex", 2, 900))
            scheduled_scan(config, "codex", clock, 2)
            self.assertFalse(clock.due("codex", 3, 900))
            self.assertTrue(clock.due("codex", 902, 900))

    def test_timeout_kills_grandchild_holding_stdout_and_next_worker_runs(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = pathlib.Path(folder) / "child.pid"
            child = "import pathlib,os,time;pathlib.Path(%r).write_text(str(os.getpid()));time.sleep(20)" % str(marker)
            parent = "import subprocess,sys,time;subprocess.Popen([sys.executable,'-c',%r]);time.sleep(20)" % child
            popen = subprocess.Popen

            def launch_ready(*args, **kwargs):
                process = popen(*args, **kwargs)
                # Establish the condition under test before allowing cleanup.
                # Cold Windows interpreter startup can otherwise consume the
                # entire 1.5-second deadline without creating a grandchild.
                deadline = time.monotonic() + 2
                while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(.01)
                return process

            started = time.monotonic()
            with patch('bounded_process.subprocess.Popen', side_effect=launch_ready):
                result = run_bounded([sys.executable, "-c", parent], timeout=1.5)
            self.assertTrue(result["timed_out"])
            self.assertLess(time.monotonic() - started, 4)
            self.assertTrue(marker.exists(), f"Fixture grandchild must actually have started: {result}")
            self.assertFalse(psutil.pid_exists(int(marker.read_text())))
            next_result = run_bounded([sys.executable, "-c", "print('next scan works')"], timeout=2)
            self.assertEqual(next_result["stdout"].strip(), "next scan works")
            self.assertFalse(next_result["timed_out"])

    def test_output_and_error_exit_survive_without_waiting_for_pipe_eof(self):
        result = run_bounded([sys.executable, "-c", "import sys;print('observation');print('failure',file=sys.stderr);sys.exit(2)"], timeout=2)
        self.assertEqual(result["returncode"], 2)
        self.assertIn("observation", result["stdout"])
        self.assertIn("failure", result["stderr"])

    def test_store_update_keeps_same_identity_but_not_another_app(self):
        old = r"C:\Program Files\WindowsApps\OpenAI.Codex_26.1016.8622.0_x64__2p2nqsd0c76g0\app\ChatGPT.exe"
        new = old.replace("26.1016.8622.0", "26.1021.3705.0")
        self.assertTrue(executable_matches(new, old))
        for fake in [new.replace("OpenAI.Codex", "Other.Codex"), new.replace("2p2nqsd0c76g0", "otherpublisher"), new.replace("ChatGPT.exe", "Other.exe"), new.replace("C:\\Program Files", "C:\\Temp"), new.replace("x64", "arm64")]:
            self.assertFalse(executable_matches(fake, old), fake)
        self.assertTrue(executable_matches(r"C:\test\python.exe", r"C:\test\python.exe"))
        self.assertFalse(executable_matches(r"C:\other\python.exe", r"C:\test\python.exe"))

    def test_timeout_is_logged_with_stack_and_never_reported_as_success(self):
        result = {"timed_out": True, "elapsed_seconds": 12.02, "stderr": "native_controls blocked", "stdout": "", "returncode": 1}
        with patch("question_supervisor.run_bounded", return_value=result), patch("question_supervisor.log") as log:
            observation = bounded_worker("question_desktop.py", "--app", "codex")
        self.assertIn("Timeout", observation["error"])
        self.assertEqual(log.call_args.args[0]["stack"], "native_controls blocked")

    def test_question_success_does_not_hide_failed_consent_scan(self):
        config = {"enabled": True, "idle_seconds_before_actions": 0,
                  "apps": {"codex": {"approvals_enabled": True}}, "chat_scan_seconds": 900}
        clock = ScanClock()
        with patch("question_supervisor.run_adapter", return_value={"windows": 1}), patch("question_supervisor.bounded_worker", return_value={"error": "native timeout"}), patch("question_supervisor.log"):
            self.assertFalse(scheduled_scan(config, "codex", clock, 0)["ready"])
        self.assertEqual(clock.remaining("codex", 0, 900), 30)

    def test_pc_in_use_allows_only_passive_scan_and_pause_prevents_it(self):
        config = {"enabled": True, "idle_seconds_before_actions": 30,
                  "apps": {"codex": {"approvals_enabled": True}}, "chat_scan_seconds": 900}
        with patch("automation_policy.idle_seconds", return_value=0), patch("question_supervisor.run_adapter", return_value={"windows": 1}) as questions, patch("question_supervisor.bounded_worker", return_value={"windows": 1, "invoked": False}) as approvals, patch("question_supervisor.log"):
            self.assertIsNone(scheduled_scan(config, "codex", ScanClock(), 0))
            self.assertTrue(scheduled_scan(config, "codex", ScanClock(), 0, inspect_only=True)["ready"])
            questions.assert_called_once_with("codex", act=False)
            approvals.assert_called_once_with("approval_desktop.py", "--app", "codex", "--inspect-only")
            config["paused"] = True
            self.assertIsNone(scheduled_scan(config, "codex", ScanClock(), 0, inspect_only=True))
            self.assertEqual(questions.call_count, 1)


if __name__ == "__main__":
    unittest.main()
