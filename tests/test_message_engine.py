import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from message_engine import TimerStore, tick, validate_job


class Button:
    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail

    def GetInvokePattern(self):
        return self

    def Invoke(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError("crash during send")


class TimerTests(unittest.TestCase):
    def test_history_includes_only_confirmed_legacy_receipts(self):
        self.store.db.execute('CREATE TABLE connector_deliveries (job_id TEXT, confirmed_at REAL, receipt TEXT)')
        self.store.db.executemany('INSERT INTO connector_deliveries VALUES (?,?,?)', [('test', 100, 'real receipt reference'), ('test', None, None)])
        with patch('message_engine.load_jobs', return_value=[self.job]):
            history = self.store.history('test')
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]['event'], 'Consegnato con ricevuta')
        self.store.event(self.job, 'Consegnato con ricevuta', 'new journal', at=100)
        with patch('message_engine.load_jobs', return_value=[self.job]):
            self.assertEqual(len(self.store.history('test')), 1)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = TimerStore(Path(self.temp.name) / "state.sqlite3")
        self.job = dict(id="test", version="v1", enabled=True, app="codex", title="Test", group="Recenti", message="hello", interval_seconds=60, activated_at=0)
        self.config = {"apps": {"codex": {"enabled": True}}}

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def run_tick(self, now, adapter):
        with patch("message_engine.load_jobs", return_value=[self.job]):
            return tick(self.config, self.store, now=now, adapter=adapter)

    def test_first_send_waits_and_late_wake_does_not_burst(self):
        button = Button()
        def adapter(config, job, delivery):
            return {"action": "ready", "button": button} if delivery["drafted"] else {"action": "drafted"}
        self.assertEqual(self.run_tick(0, adapter)["action"], "idle")
        self.assertEqual(self.run_tick(59, adapter)["action"], "idle")
        self.assertEqual(self.run_tick(61, adapter)["action"], "drafted")
        self.assertEqual(self.run_tick(62, adapter)["action"], "sent_periodic")
        self.assertEqual(self.run_tick(63, adapter)["action"], "idle")
        self.run_tick(600, adapter)
        self.run_tick(601, adapter)
        self.assertEqual(button.calls, 2)
        self.assertEqual(self.store.rows()[0]["due"], 661)

    def test_delayed_initialization_keeps_activation_deadline(self):
        self.job["activated_at"] = 100
        state = self.store.prepare(self.job, 1000)
        self.assertEqual(state["due"], 160)

    def test_new_version_discards_prior_schedule_and_last_sent(self):
        self.store.prepare(self.job, 0)
        self.store.update(self.job, "drafted", "old", due=500, sent=400)
        self.job.update(version="v2", activated_at=1000)
        state = self.store.prepare(self.job, 1100)
        self.assertEqual(state["due"], 1060)
        self.assertEqual(state["phase"], "idle")
        self.assertIsNone(state["last_sent"])

    def test_uncertain_send_is_never_retried_after_crash(self):
        self.store.prepare(self.job, 0)
        self.store.update(self.job, "drafted", "draft")
        button = Button(fail=True)
        adapter = lambda *args: {"action": "ready", "button": button}
        with self.assertRaises(RuntimeError):
            self.run_tick(61, adapter)
        self.assertEqual(self.run_tick(1000, adapter)["action"], "idle")
        self.assertEqual(button.calls, 1)
        self.assertEqual(self.store.rows()[0]["phase"], "uncertain")

    def test_pause_between_preparation_and_send(self):
        self.store.prepare(self.job, 0)
        self.store.update(self.job, "drafted", "draft")
        button = Button()
        with patch("message_engine.load_jobs", side_effect=[[self.job], [{**self.job, "enabled": False}]]):
            result = tick(self.config, self.store, now=61, adapter=lambda *args: {"action": "ready", "button": button})
        self.assertEqual(result["action"], "cancelled")
        self.assertEqual(button.calls, 0)

    def test_one_shot_finishes(self):
        self.job["repeat"] = False
        self.store.prepare(self.job, 0)
        self.store.update(self.job, "drafted", "draft")
        button = Button()
        adapter = lambda *args: {"action": "ready", "button": button}
        self.run_tick(61, adapter)
        self.run_tick(1000, adapter)
        self.assertEqual(button.calls, 1)
        self.assertEqual(self.store.rows()[0]["phase"], "finished")

    def test_busy_chat_retains_due_job_without_sending(self):
        self.store.prepare(self.job, 0)
        self.run_tick(61, lambda *args: {"action": "none", "reason": "Chat in elaborazione"})
        self.assertEqual(self.store.rows()[0]["due"], 60)
        self.assertTrue(self.store.retry_pending(self.job, 62))
        self.assertIsNone(self.store.rows()[0]["last_sent"])

    def test_global_pause_blocks_even_an_enabled_due_timer(self):
        self.store.prepare(self.job, 0)
        adapter = patch("chat_desktop.step")
        with adapter as native:
            with patch("message_engine.load_jobs", return_value=[self.job]):
                result = tick({**self.config, "paused": True}, self.store, now=999)
        native.assert_not_called()
        self.assertEqual(result["action"], "paused")

    def test_unavailable_app_waits_without_touching_its_chat(self):
        self.config["temporarily_unavailable_apps"] = ["codex"]
        with patch("message_engine.load_jobs", return_value=[self.job]):
            with patch("chat_desktop.step") as native:
                result = tick(self.config, self.store, now=999)
        native.assert_not_called()
        self.assertEqual(result["action"], "idle")

    def test_available_app_can_run_while_the_other_is_still_starting(self):
        other = {**self.job, "id": "claude", "app": "claude"}
        self.store.prepare(other, 0)
        self.config["temporarily_unavailable_apps"] = ["codex"]
        calls = []
        def adapter(config, job, delivery):
            calls.append(job["app"])
            return {"action": "none", "reason": "Chat in elaborazione"}
        with patch("message_engine.load_jobs", return_value=[self.job, other]):
            tick(self.config, self.store, now=999, adapter=adapter)
        self.assertEqual(calls, ["claude"])

    def test_background_selection_keeps_timer_due_for_next_step(self):
        self.store.prepare(self.job, 0)
        self.run_tick(61, lambda *args: {"action": "selected_chat"})
        self.assertEqual(self.store.rows()[0]["due"], 60)
        self.assertEqual(self.run_tick(61.5, lambda *args: {"action": "drafted"})["action"], "drafted")

    def test_empty_message_and_fast_spam_interval_are_invalid(self):
        for change in ({"message": " \n"}, {"interval_seconds": 0}, {"interval_seconds": float("nan")}):
            with self.assertRaises(ValueError):
                validate_job({**self.job, **change})


if __name__ == "__main__":
    unittest.main()
