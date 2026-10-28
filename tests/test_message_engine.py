import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
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

    def test_claude_legacy_missing_present_flag_matches_the_editor_default(self):
        self.job['app'] = 'claude'
        delivery = Mock()
        delivery.reconcile.return_value = None
        delivery.cleanup_needed.return_value = False
        delivery.current.return_value = None
        with patch('message_engine.load_jobs', return_value=[self.job]), \
             patch('chat_desktop.step', return_value={'action': 'none', 'reason': 'Test local only'}), \
             patch('claude_delivery.Delivery', return_value=delivery), \
             patch('automation_policy.native_delivery_config', return_value={'idle_seconds_before_actions': 0}) as policy:
            tick(self.config, self.store, now=61)
        self.assertTrue(policy.call_args.kwargs['allow_present'])


class LocalNavigationTests(unittest.TestCase):
    """Synthetic sender pipeline, private DB/history, native controls mocked."""
    def setUp(self):
        import json
        from codex_delivery import Delivery
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / 'logs').mkdir()
        self.history = self.root / 'history.jsonl'
        self.thread = '11111111-1111-4111-8111-111111111111'
        self.history.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': self.thread}}) + '\n')
        self.store = TimerStore(self.root / 'state.sqlite3')
        self.job = dict(id='synthetic', version='v1', enabled=True, app='codex', title='Exact target',
                        group='Project', message='Literal', interval_seconds=60, activated_at=0,
                        send_when_active=True, send_when_present=True, thread_id=self.thread,
                        transport='codex_app', ambiguous=False)
        self.config = {'paused': False, 'idle_seconds_before_actions': 0,
                       'native_background_delivery': True, 'native_navigation_idle_seconds': 30}
        self.bridge = Delivery(self.store, lambda: self.config,
                               lambda: [dict(self.job)], lambda: True)
        self.metadata = lambda *_: {'id': self.thread, 'rollout_path': str(self.history)}
        self.owned_root = patch('codex_local_sender.ROOT', self.root)
        self.owned_root.start()

    def tearDown(self):
        self.owned_root.stop()
        self.store.db.close()
        self.temporary.cleanup()

    def sender_tick(self, adapter=None, observe=None, metadata=None):
        from codex_local_sender import tick as sender
        return sender(self.store, self.bridge, now=61, metadata=metadata or self.metadata,
                      observe=observe or (lambda *_: ('Exact target', 'active')), adapter=adapter)

    def test_codex_pipeline_selects_background_target_and_preserves_unclaimed_timer(self):
        control = Mock()
        with patch('chat_desktop.snapshot', return_value=([], 'Other chat')), \
             patch('chat_desktop.entries', return_value=[({**self.job, 'status': 'Working'}, control)]), \
             patch('automation_policy.idle_seconds', return_value=0), \
             patch('automation_policy.foreground_app_state', return_value=False):
            result = self.sender_tick(adapter=Mock(side_effect=AssertionError('No draft before selection')),
                                      observe=lambda *_: ('Other chat', 'active'))
        self.assertEqual(result['action'], 'selected_chat')
        control.GetInvokePattern.return_value.Invoke.assert_called_once_with()
        self.assertEqual(self.store.rows()[0]['phase'], 'idle')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM connector_deliveries').fetchone()[0], 0)

    def test_codex_pipeline_records_the_specific_foreground_reason(self):
        control = Mock()
        with patch('chat_desktop.snapshot', return_value=([], 'Other chat')), \
             patch('chat_desktop.entries', return_value=[(self.job, control)]), \
             patch('automation_policy.idle_seconds', return_value=0), \
             patch('automation_policy.foreground_app_state', return_value=True):
            result = self.sender_tick(observe=lambda *_: ('Other chat', 'active'))
        self.assertIn('Applicazione in uso su un’altra chat', result['reason'])
        self.assertEqual(self.store.rows()[0]['detail'], result['reason'])
        control.GetInvokePattern.return_value.Invoke.assert_not_called()

    def test_missing_codex_draft_stops_before_native_observation_or_redraft(self):
        self.sender_tick(adapter=lambda *_: {'action': 'drafted'})
        self.store.update(self.job, 'uncertain', 'Bozza scomparsa: verifica manuale prima di ripetere')
        native = Mock(side_effect=AssertionError('Never retry a disappeared draft'))
        result = self.sender_tick(adapter=native, observe=native, metadata=native)
        self.assertEqual(result['action'], 'uncertain')
        self.assertIn('Bozza scomparsa', result['reason'])
        native.assert_not_called()
        stage = self.store.db.execute('SELECT stage FROM local_codex_deliveries').fetchone()[0]
        self.assertEqual(stage, 'drafted')

    def test_codex_pause_and_changed_background_option_stop_before_send(self):
        for change in ('pause', 'option'):
            with self.subTest(change=change):
                if change == 'option':
                    self.config['paused'] = False
                else:
                    self.sender_tick(adapter=lambda *_: {'action': 'drafted'})
                button = Mock()
                def ready(*_):
                    if change == 'pause':
                        self.config['paused'] = True
                    else:
                        self.job['send_when_present'] = False
                    return {'action': 'ready', 'button': button}
                result = self.sender_tick(adapter=ready)
                self.assertIn(result['action'], {'paused_or_changed', 'changed_or_disabled'})
                button.GetInvokePattern.assert_not_called()
                self.assertEqual(self.store.db.execute('SELECT stage FROM local_codex_deliveries').fetchone()[0], 'drafted')

    def test_codex_pause_during_last_observation_is_checked_before_send(self):
        self.sender_tick(adapter=lambda *_: {'action': 'drafted'})
        observations = 0
        def observe(*_):
            nonlocal observations
            observations += 1
            if observations == 2:
                self.config['paused'] = True
            return 'Exact target', 'active'
        button = Mock()
        result = self.sender_tick(adapter=lambda *_: {'action': 'ready', 'button': button}, observe=observe)
        self.assertEqual(result['action'], 'none')
        self.assertIn('pausa', result['reason'])
        button.GetInvokePattern.assert_not_called()
        self.assertEqual(self.store.db.execute('SELECT stage FROM local_codex_deliveries').fetchone()[0], 'drafted')

    def test_codex_legacy_missing_present_flag_matches_the_editor_default(self):
        from codex_delivery import Delivery
        from codex_local_sender import tick as sender
        self.job.pop('send_when_present')
        def bridge(store, config_reader):
            return Delivery(store, config_reader, lambda: [dict(self.job)], lambda: True)
        with patch('codex_local_sender.native_delivery_config', return_value=self.config) as policy, \
             patch('codex_local_sender.Delivery', side_effect=bridge):
            result = sender(self.store, now=0, metadata=self.metadata,
                            observe=Mock(side_effect=AssertionError('Timer not due')))
        self.assertEqual(result['action'], 'idle')
        self.assertTrue(policy.call_args.kwargs['allow_present'])


if __name__ == "__main__":
    unittest.main()
