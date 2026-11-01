from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from codex_delivery import Delivery
from codex_local_sender import find_user_receipt, tick, native_delivery_config
from message_engine import TimerStore
from question_supervisor import connector_work_due


class Button:
    def __init__(self, crash=False):
        self.calls = 0
        self.crash = crash

    def GetInvokePattern(self):
        return self

    def Invoke(self):
        self.calls += 1
        if self.crash:
            raise RuntimeError("send outcome unknown")


class LocalSenderTests(unittest.TestCase):
    def test_edit_retires_only_unsent_reserved_version_and_keeps_new_deadline(self):
        self.tick(2000,lambda *_:{'action':'none','reason':'Campo messaggio non univoco'})
        token=self.store.db.execute('SELECT token FROM local_codex_deliveries').fetchone()[0]
        self.job.update(version='v2',message='New message',activated_at=2100)
        self.assertEqual(self.tick(2100,lambda *_:self.fail('not due'))['action'],'superseded_before_send')
        self.assertEqual(self.store.rows()[0]['due'],3900)
        self.assertEqual(self.tick(3899,lambda *_:self.fail('not due'))['action'],'idle')
        self.assertEqual(self.store.db.execute('SELECT stage FROM local_codex_deliveries WHERE token=?',(token,)).fetchone()[0],'cancelled_before_send')
        self.assertIsNone(self.store.db.execute('SELECT confirmed_at FROM connector_deliveries WHERE token=?',(token,)).fetchone()[0])
        self.assertEqual(self.tick(3900,lambda *_:{'action':'drafted'})['action'],'drafted')

    def test_edit_never_retires_a_draft_or_an_uncertain_send(self):
        self.tick(2000,lambda *_:{'action':'drafted'})
        self.job.update(version='v2',message='New message',activated_at=2100)
        self.assertEqual(self.tick(2100,lambda *_:self.fail('preserve draft'))['action'],'changed_or_disabled')
        self.assertEqual(self.store.db.execute('SELECT stage FROM local_codex_deliveries').fetchone()[0],'drafted')

    def test_native_read_error_is_persisted_and_cleared_only_by_real_receipt(self):
        self.metadata=lambda job:(_ for _ in ()).throw(ValueError('Interfaccia non disponibile'))
        with self.assertRaisesRegex(ValueError,'Interfaccia non disponibile'):
            self.tick(2000,lambda *_:self.fail('must not send'))
        self.assertIn('Interfaccia non disponibile',self.store.rows()[0]['delivery_error'])
        self.assertEqual(self.store.rows()[0]['due'],1800)
        self.metadata=lambda job:{'id':'thread-1','rollout_path':str(self.path)}
        button=Button()
        adapter=lambda c,j,d:({'action':'ready','button':button} if d['drafted'] else {'action':'drafted'})
        self.assertEqual(self.tick(2001,adapter)['action'],'drafted')
        self.assertEqual(self.tick(2002,adapter)['action'],'awaiting_receipt')
        self.append(self.job['message'],at=2003)
        self.assertEqual(self.tick(2004,adapter)['action'],'confirmed')
        self.assertNotIn('delivery_error',self.store.rows()[0]);self.assertEqual(button.calls,1)

    def test_rename_after_draft_sends_once_to_the_same_id_and_receipt(self):
        button=Button();seen=[]
        def adapter(config,job,delivery):
            seen.append((job['thread_id'],job['title']))
            return {'action':'ready','button':button} if delivery['drafted'] else {'action':'drafted'}
        self.assertEqual(self.tick(2000,adapter)['action'],'drafted')
        self.metadata=lambda job:{'id':'thread-1','name':'Renamed Regia','rollout_path':str(self.path)}
        self.observe=lambda *_:('Renamed Regia','active')
        self.assertEqual(self.tick(2001,adapter)['action'],'awaiting_receipt')
        self.assertEqual(seen[-1],('thread-1','Renamed Regia'));self.assertEqual(button.calls,1)
        self.append(self.job['message'])
        self.assertEqual(self.tick(2003,adapter)['action'],'confirmed')
        self.assertEqual(button.calls,1)

    def test_background_delivery_gate_never_grants_foreground_or_paused_changes(self):
        config = {"paused": False, "idle_seconds_before_actions": 30, "apps": {"codex": {"process_path": "C:/Codex/ChatGPT.exe"}}}
        with patch("automation_policy.idle_seconds", return_value=0):
            background = native_delivery_config(lambda: config, foreground_exe="C:/Browser/chrome.exe")
            self.assertTrue(background["native_background_delivery"])
            self.assertEqual(config["idle_seconds_before_actions"], 30)
            self.assertNotIn("native_background_delivery", native_delivery_config(lambda: config, foreground_exe="C:/Codex/ChatGPT.exe"))
            config["paused"] = True
            self.assertNotIn("native_background_delivery", native_delivery_config(lambda: config, foreground_exe="C:/Browser/chrome.exe"))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "logs").mkdir()
        self.path = self.root / "history.jsonl"
        self.path.write_text(json.dumps({"type": "session_meta", "payload": {"id": "thread-1"}}) + "\n")
        self.store = TimerStore(self.root / "timer.sqlite3")
        self.config = {"paused": False, "idle_seconds_before_actions": 0}
        self.job = dict(id="regia", version="v1", app="codex", title="Regia", group="Recenti", message="Exact scheduled message", enabled=True, repeat=True, activated_at=0, interval_seconds=1800, transport="codex_app", thread_id="thread-1", send_when_active=True)
        self.bridge = Delivery(self.store, lambda: self.config, lambda: [self.job], lambda: True)
        self.metadata = lambda job: {"id": "thread-1", "rollout_path": str(self.path)}
        self.observe = lambda *args: ("Regia", "active")
        self.patch = patch("codex_local_sender.ROOT", self.root)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.store.db.close()
        self.temp.cleanup()

    def tick(self, now, adapter):
        return tick(self.store, self.bridge, now=now, metadata=self.metadata, observe=self.observe, adapter=adapter)

    def append(self, text, role="user", at=2002):
        record = {"timestamp": datetime.fromtimestamp(at, timezone.utc).isoformat(), "type": "response_item", "payload": {"type": "message", "role": role, "id": "real-message-id", "content": [{"type": "input_text", "text": text}]}}
        with self.path.open("a") as f:
            f.write(json.dumps(record) + "\n")

    def test_active_due_send_is_local_and_only_real_user_receipt_advances_timer(self):
        button = Button()
        def adapter(config, job, delivery):
            return {"action": "ready", "button": button} if delivery["drafted"] else {"action": "drafted"}
        self.assertEqual(self.tick(1799, adapter)["action"], "idle")
        self.assertEqual(self.tick(2000, adapter)["action"], "drafted")
        self.assertEqual(self.tick(2001, adapter)["action"], "awaiting_receipt")
        self.assertEqual(button.calls, 1)
        self.assertIsNone(self.store.rows()[0]["last_sent"])
        self.append(self.job["message"], role="assistant")
        self.append("Different text")
        self.append(self.job["message"], at=100)
        self.assertEqual(self.tick(2003, adapter)["action"], "awaiting_receipt")
        self.assertEqual(button.calls, 1)
        self.append(self.job["message"], at=2004)
        self.assertEqual(self.tick(2005, adapter)["action"], "confirmed")
        self.assertEqual(self.store.rows()[0]["due"], 3805)
        self.assertEqual(self.tick(2006, adapter)["action"], "idle")
        self.assertEqual(button.calls, 1)

    def test_unknown_send_outcome_is_never_retried_after_restart(self):
        button = Button(crash=True)
        self.tick(2000, lambda *args: {"action": "drafted"})
        with self.assertRaises(RuntimeError):
            self.tick(2001, lambda *args: {"action": "ready", "button": button})
        self.assertEqual(self.tick(9000, lambda *args: self.fail("No resend after uncertain result"))["action"], "awaiting_receipt")
        self.assertEqual(button.calls, 1)

    def test_pause_edit_and_busy_policy_prevent_send(self):
        self.job["send_when_active"] = False
        self.assertEqual(self.tick(2000, lambda *args: self.fail("busy"))["action"], "waiting_for_chat")
        self.job["send_when_active"] = True
        self.tick(2000, lambda *args: {"action": "drafted"})
        self.config["paused"] = True
        self.assertEqual(self.tick(2001, lambda *args: self.fail("paused"))["action"], "paused")
        self.config["paused"] = False
        self.job["message"] = "Edited by user"
        self.assertEqual(self.tick(2002, lambda *args: self.fail("edited"))["action"], "changed_or_disabled")

    def test_receipt_requires_correct_thread_header_and_complete_line(self):
        digest = hashlib.sha256(self.job["message"].encode()).hexdigest()
        offset = self.path.stat().st_size
        self.append(self.job["message"])
        with self.assertRaises(ValueError):
            find_user_receipt(self.path, "wrong-thread", digest, 2000, offset, 2003)
        raw = self.path.read_bytes()
        self.path.write_bytes(raw[:-1])
        receipt, cursor = find_user_receipt(self.path, "thread-1", digest, 2000, offset, 2003)
        self.assertIsNone(receipt)
        self.assertEqual(cursor, offset)

    def test_explicit_verification_allows_exactly_one_early_delivery(self):
        button = Button()
        def adapter(config, job, delivery):
            return {"action": "ready", "button": button} if delivery["drafted"] else {"action": "drafted"}
        request = {"action": "one_authorized_delivery", "request_id": "explicit-user-request", "job_id": self.job["id"], "version": self.job["version"], "created_at": 100, "expires_at": 5000}
        (self.root / "logs/codex-local-verify-request.json").write_text(json.dumps(request))
        self.assertEqual(self.tick(1000, adapter)["action"], "drafted")
        self.assertEqual(self.tick(1001, adapter)["action"], "awaiting_receipt")
        self.append(self.job["message"], at=1002)
        self.assertEqual(self.tick(1003, adapter)["action"], "confirmed")
        self.assertEqual(self.tick(1004, adapter)["action"], "idle")
        self.assertEqual(button.calls, 1)

    def test_native_terminal_lf_receipt_survives_edit_without_resetting_new_timer(self):
        button = Button()
        self.tick(2000, lambda *args: {"action": "drafted"})
        self.tick(2001, lambda *args: {"action": "ready", "button": button})
        old_message = self.job["message"]
        self.job.update(version="v2", message="New saved message", activated_at=2002)
        self.store.prepare(self.job, 2002)
        self.append(old_message + "\n", at=2003)
        self.assertEqual(self.tick(2004, lambda *args: self.fail("No second send"))["action"], "confirmed")
        current = self.store.rows()[0]
        self.assertEqual(current["version"], "v2")
        self.assertEqual(current["due"], 3802)
        self.assertIsNone(current["last_sent"])

    def test_future_timer_needs_no_adapter_but_due_pending_and_explicit_request_do(self):
        self.tick(1000, lambda *args: self.fail("not due"))
        database = (self.root / "timer.sqlite3").resolve()
        self.assertFalse(connector_work_due([self.job], database, now=1799))
        self.assertTrue(connector_work_due([self.job], database, now=1800))
        self.tick(2000, lambda *args: {"action": "drafted"})
        self.assertTrue(connector_work_due([self.job], database, now=2001))
        self.assertFalse(connector_work_due([{**self.job, "enabled": False}], database, now=2001))


if __name__ == "__main__":
    unittest.main()
