from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from codex_delivery import Delivery
from message_engine import TimerStore, tick


class ConnectorDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.store = TimerStore(Path(self.folder.name) / "timers.sqlite3")
        self.config = {"idle_seconds_before_actions": 0, "paused": False}
        self.job = dict(id="regia", version="v1", app="codex", title="Regia", group="Recenti", message="Saved message", enabled=True, repeat=True, activated_at=0, interval_seconds=1800, transport="codex_app", thread_id="thread-1")
        self.bridge = Delivery(self.store, lambda: self.config, lambda: [self.job], lambda: True)

    def tearDown(self):
        self.store.db.close()
        self.folder.cleanup()

    def test_overdue_due_survives_ui_failure_and_reservation_is_unique(self):
        self.assertEqual(self.bridge.plan(2000)["jobs"][0]["message"], "Saved message")
        claim = self.bridge.claim("regia", "v1", "idle", 2000)
        self.assertEqual(claim["action"], "send")
        self.assertNotEqual(self.bridge.claim("regia", "v1", "idle", 2001)["action"], "send")
        self.assertEqual(self.bridge.plan(5000)["jobs"], [])
        self.assertEqual(len(self.bridge.plan(5000)["pending_confirmation"]), 1)

    def test_live_pause_edit_and_app_closure_prevent_send(self):
        claim = self.bridge.claim("regia", "v1", "idle", 2000)
        self.config["paused"] = True
        self.assertEqual(self.bridge.ready(claim["token"])["action"], "paused")
        self.config["paused"] = False
        self.job["message"] = "Changed"
        self.assertEqual(self.bridge.ready(claim["token"])["action"], "changed_or_disabled")
        self.bridge.running = lambda: False
        self.assertEqual(self.bridge.plan(2000)["action"], "paused")

    def test_resolved_old_token_cannot_be_sent_or_confirmed_by_a_future_identical_message(self):
        from delivery_recovery import ensure_schema
        claim=self.bridge.claim('regia','v1','idle',2000);token=claim['token']
        ensure_schema(self.store.db)
        self.store.db.execute('INSERT INTO legacy_delivery_resolutions VALUES(?,?,?,?,?,?,?,?,?)',
            (token,'codex','thread-1','regia','v1',2010,1800,3600,'future_only'));self.store.db.commit()
        before=self.store.rows()[0].copy()
        data={'thread':{'id':'thread-1'},'turns':[{'id':'future','startedAt':3000,
            'items':[{'type':'userMessage','id':'future-message','content':[{'type':'text','text':'Saved message'}]}]}]}
        self.assertEqual('resolved_without_confirmation',self.bridge.ready(token)['action'])
        self.assertEqual([],self.bridge.plan(5000)['pending_confirmation'])
        self.assertEqual('resolved_without_confirmation',self.bridge.confirm_readback(token,data,3010)['action'])
        self.assertEqual('resolved_without_confirmation',self.bridge.confirm(token,'future-proof',3010)['action'])
        self.assertIsNone(self.store.db.execute('SELECT confirmed_at FROM connector_deliveries WHERE token=?',(token,)).fetchone()[0])
        self.assertEqual(before,self.store.rows()[0])

    def test_busy_chat_is_waiting_not_a_false_send(self):
        self.assertEqual(self.bridge.claim("regia", "v1", "active", 2000)["action"], "waiting_for_chat")
        state = self.store.rows()[0]
        self.assertIsNone(state["last_sent"])
        self.assertEqual(state["phase"], "idle")

    def test_authorized_active_chat_sends_only_when_due_and_reserves_once(self):
        self.job["send_when_active"] = True
        self.assertEqual(self.bridge.claim("regia", "v1", "active", 1799)["action"], "not_due_or_paused")
        self.assertTrue(self.bridge.plan(2000)["jobs"][0]["send_when_active"])
        claim = self.bridge.claim("regia", "v1", "active", 2000)
        self.assertEqual(claim["action"], "send")
        self.assertEqual(self.bridge.ready(claim["token"])["action"], "send")
        self.assertNotEqual(self.bridge.claim("regia", "v1", "active", 2001)["action"], "send")
        self.assertIsNone(self.store.rows()[0]["last_sent"])
        self.bridge.confirm(claim["token"], "read_thread:real-receipt", 2010)
        self.assertEqual(self.store.rows()[0]["due"], 3810)

    def test_active_override_does_not_allow_unknown_status_or_paused_app(self):
        self.job["send_when_active"] = True
        for status in ("unknown", "error", "systemError", ""):
            self.assertEqual(self.bridge.claim("regia", "v1", status, 2000)["action"], "waiting_for_chat")
        self.config["paused"] = True
        self.assertEqual(self.bridge.claim("regia", "v1", "active", 2000)["action"], "not_due_or_paused")
        self.config["paused"] = False
        self.bridge.running = lambda: False
        self.assertEqual(self.bridge.claim("regia", "v1", "active", 2000)["action"], "not_due_or_paused")

    def test_receipt_updates_same_timer_without_burst_or_duplicate(self):
        claim = self.bridge.claim("regia", "v1", "idle", 2000)
        self.assertEqual(self.bridge.ready(claim["token"])["action"], "send")
        self.assertEqual(self.bridge.confirm(claim["token"], "read_thread:turn-123", 2010)["action"], "confirmed")
        state = self.store.rows()[0]
        self.assertEqual(state["last_sent"], 2010)
        self.assertEqual(state["due"], 3810)
        self.assertEqual(self.bridge.plan(3809)["jobs"], [])
        self.assertEqual(self.bridge.confirm(claim["token"], "same", 4000)["action"], "unknown_or_already_confirmed")

    def test_new_job_version_is_not_overwritten_by_old_receipt(self):
        claim = self.bridge.claim("regia", "v1", "idle", 2000)
        self.job.update(version="v2", activated_at=2100)
        self.store.prepare(self.job, 2100)
        self.bridge.confirm(claim["token"], "old-receipt", 2110)
        self.assertIsNone(self.store.rows()[0]["last_sent"])

    def test_receipt_matches_real_connector_message_not_an_assistant_echo(self):
        claim = self.bridge.claim("regia", "v1", "idle", 2000)
        readback = {"thread": {"id": "thread-1"}, "turns": [{"id": "turn-1", "startedAt": 2001, "items": [{"type": "agentMessage", "id": "echo", "text": self.job["message"]}]}]}
        self.assertEqual(self.bridge.confirm_readback(claim["token"], readback, 2010)["action"], "receipt_not_found")
        item = {"type": "functionCallOutput", "id": "incoming", "name": "send_message_to_thread", "namespace": "codex_app", "output": {"text": "<codex_delegation>\n<source_thread_id>source</source_thread_id>\n<input>Saved message</input>\n</codex_delegation>", "truncated": False}}
        readback["turns"][0]["items"] = [item]
        readback["thread"]["id"] = "other-thread"
        self.assertEqual(self.bridge.confirm_readback(claim["token"], readback, 2010)["action"], "receipt_mismatch")
        readback["thread"]["id"] = "thread-1"
        self.assertEqual(self.bridge.confirm_readback(claim["token"], readback, 2010)["action"], "confirmed")

    def test_old_or_truncated_receipt_cannot_mark_sent(self):
        claim = self.bridge.claim("regia", "v1", "idle", 2000)
        item = {"type": "functionCallOutput", "id": "old", "name": "send_message_to_thread", "namespace": "codex_app", "output": {"text": "<codex_delegation><input>Saved message</input></codex_delegation>", "truncated": False}}
        data = {"thread": {"id": "thread-1"}, "turns": [{"id": "old", "startedAt": 1000, "items": [item]}]}
        self.assertEqual(self.bridge.confirm_readback(claim["token"], data)["action"], "receipt_not_found")
        data["turns"][0]["startedAt"] = 2001
        item["output"]["truncated"] = True
        self.assertEqual(self.bridge.confirm_readback(claim["token"], data)["action"], "receipt_not_found")

    def test_active_delivery_receipt_uses_item_time_inside_older_turn(self):
        claim = self.bridge.claim("regia", "v1", "idle", 2000)
        # RFC9562 UUIDv7: Unix milliseconds occupy the leading 48 bits.
        from uuid import UUID
        item_id = "fco_" + str(UUID(int=(2001000 << 80) | (7 << 76) | (2 << 62) | 1))
        item = {"type": "functionCallOutput", "id": item_id, "name": "send_message_to_thread", "namespace": "codex_app", "output": {"text": "<codex_delegation><input>Saved message</input></codex_delegation>", "truncated": False}}
        data = {"thread": {"id": "thread-1"}, "turns": [{"id": "running-turn", "startedAt": 1000, "items": [item]}]}
        item["output"]["truncated"] = True
        self.assertEqual(self.bridge.confirm_readback(claim["token"], data, 2010)["action"], "receipt_not_found")
        item["output"]["truncated"] = False
        self.assertEqual(self.bridge.confirm_readback(claim["token"], data, 2010)["action"], "confirmed")
        self.assertEqual(data["turns"][0]["startedAt"], 1000)
        self.assertEqual(self.store.rows()[0]["due"], 3810)

    def test_item_time_rejects_replay_future_or_unusable_id_in_older_turn(self):
        from uuid import UUID
        claim = self.bridge.claim("regia", "v1", "idle", 2000)
        item = {"type": "functionCallOutput", "name": "send_message_to_thread", "namespace": "codex_app", "output": {"text": "<codex_delegation><input>Saved message</input></codex_delegation>", "truncated": False}}
        data = {"thread": {"id": "thread-1"}, "turns": [{"id": "running-turn", "startedAt": 1000, "items": [item]}]}
        for timestamp in (1000000, 2200000):
            item["id"] = "fco_" + str(UUID(int=(timestamp << 80) | (7 << 76) | (2 << 62) | 1))
            self.assertEqual(self.bridge.confirm_readback(claim["token"], data, 2010)["action"], "receipt_not_found")
        for value in ("fco_bad", "fco_11111111-2222-4333-8444-555555555555"):
            item["id"] = value
            self.assertEqual(self.bridge.confirm_readback(claim["token"], data, 2010)["action"], "receipt_not_found")
        # An old UUIDv7 receipt remains old even if copied into a recent turn.
        item["id"] = "fco_" + str(UUID(int=(1000000 << 80) | (7 << 76) | (2 << 62) | 1))
        data["turns"][0]["startedAt"] = 2001
        self.assertEqual(self.bridge.confirm_readback(claim["token"], data, 2010)["action"], "receipt_not_found")

    def test_native_worker_never_duplicates_connector_timer(self):
        with patch("message_engine.load_jobs", return_value=[self.job]):
            calls = []
            result = tick(self.config, self.store, now=5000, adapter=lambda *args: calls.append(args))
        self.assertEqual(result["action"], "idle")
        self.assertEqual(calls, [])

    def test_transport_switch_cancels_already_prepared_native_send(self):
        job = {**self.job, "transport": "desktop"}
        self.store.prepare(job, 0)
        self.store.update(job, "drafted", "old native draft")
        from unittest.mock import Mock
        button = Mock()
        with patch("message_engine.load_jobs", side_effect=[[job], [self.job]]):
            result = tick(self.config, self.store, now=2000, adapter=lambda *args: {"action": "ready", "button": button})
        self.assertEqual(result["action"], "cancelled")
        button.GetInvokePattern.assert_not_called()


if __name__ == "__main__":
    unittest.main()
