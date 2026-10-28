import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace
from datetime import datetime, timezone

from claude_delivery import Delivery, digest, read_receipt, receipt_matches, locate
from message_engine import TimerStore, tick
from chat_desktop import step


class ClaudeDeliveryTests(unittest.TestCase):
    def test_human_mid_turn_requires_exact_enqueue_attachment_and_absorption_ids(self):
        import copy
        stamp = datetime.now(timezone.utc).isoformat()
        command_id = '35a7f4fc-b5d4-43c3-97be-5ee540c6a48c'
        delivery_id = '84a226de-829a-45a2-a298-8344b23c900d'
        text = self.job['message'].rstrip(' ')
        rows = [dict(type='queue-operation',operation='enqueue',timestamp=stamp,sessionId='session',content=text),
                dict(type='attachment',uuid='370c5f17-93b0-4d4e-95ef-ed389b5af3e3',timestamp=stamp,sessionId='session',
                     attachment=dict(type='queued_command',prompt=text,source_uuid=command_id,delivery_id=delivery_id,
                                     commandMode='prompt',origin={'kind':'human'},humanTurn=True,timestamp=stamp)),
                dict(type='queue-operation',operation='remove',reason='absorbed_mid_turn',timestamp=stamp,sessionId='session',
                     content=text,commandUuid=command_id,deliveryId=delivery_id)]
        for invalid in (None, 'missing_absorption', 'wrong_delivery', 'nonhuman', 'text_changed', 'different_session'):
            sample = copy.deepcopy(rows)
            if invalid == 'missing_absorption':sample.pop()
            elif invalid == 'wrong_delivery':sample[-1]['deliveryId']='different'
            elif invalid == 'nonhuman':sample[1]['attachment']['humanTurn']=False
            elif invalid == 'text_changed':sample[1]['attachment']['prompt']+='changed'
            elif invalid == 'different_session':sample[1]['sessionId']='other'
            self.history.write_text(''.join(json.dumps(row)+'\n' for row in sample),encoding='utf8')
            receipt = read_receipt(self.identity,self.job['message'],0)
            if invalid is None:
                self.assertEqual(receipt['origin'],'human_mid_turn')
                self.assertEqual(receipt['delivery_id'],delivery_id)
            else:self.assertIsNone(receipt,invalid)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.history = self.root / 'history.jsonl'
        self.history.write_text('', encoding='utf-8')
        self.identity = {'history': str(self.history), 'session': 'session', 'index': 'index'}
        self.job = dict(id='job', version='v1', app='claude', title='Test', group='Project',
                        message='Messaggio già autorizzato. ', enabled=True,
                        interval_seconds=900, activated_at=0, send_when_active=True)
        self.store = TimerStore(self.root / 'messages.sqlite3')
        self.state = self.store.prepare(self.job, 0)
        self.locator = patch('claude_delivery.locate', return_value=self.identity)
        self.locator.start()
        self.delivery = Delivery(self.store, self.root)

    def tearDown(self):
        self.locator.stop()
        self.store.db.close()
        self.temp.cleanup()

    def user_message(self, uid='one', text=None, **changes):
        o = {'type': 'user', 'sessionId': 'session', 'uuid': uid,
             'timestamp': datetime.now(timezone.utc).isoformat(),
             'message': {'role': 'user', 'content': self.job['message'].rstrip(' ') if text is None else text}}
        o.update(changes)
        with self.history.open('a', encoding='utf-8') as f:
            f.write(json.dumps(o, ensure_ascii=False) + '\n')
        return o

    def test_only_known_terminal_space_trim_is_accepted(self):
        self.assertTrue(receipt_matches('già. ', 'già.'))
        for expected, actual in [(' già.', 'già.'), ('già. ', 'gia.'), ('a  b ', 'a b'), ('a\n', 'a')]:
            self.assertFalse(receipt_matches(expected, actual))

    def test_two_real_receipts_release_two_separate_reservations(self):
        for uid in ['one', 'two']:
            self.delivery.prepare(self.job)
            self.delivery.begin_send(self.job)
            self.store.update(self.job, 'uncertain', 'reserved')
            self.user_message(uid)
            result = self.delivery.reconcile(self.job, self.state)
            self.assertEqual(result['action'], 'receipt_confirmed')
            self.delivery.archive(self.job, self.job['message'])
            row = self.delivery.current(self.job)
            self.assertEqual(json.loads(Path(row['backup']).read_text(encoding='utf-8'))['text'], self.job['message'])
            self.delivery.cleanup_done(self.job, 'draft_cleared')
        rows = list(self.store.db.execute('SELECT receipt_uuid,stage FROM claude_timer_deliveries'))
        self.assertEqual([(r['receipt_uuid'], r['stage']) for r in rows], [('one', 'cleared'), ('two', 'cleared')])
        self.assertEqual(self.store.rows()[0]['phase'], 'idle')

    def test_invoke_or_assistant_text_does_not_prove_receipt(self):
        self.delivery.prepare(self.job); self.delivery.begin_send(self.job)
        self.user_message(type='assistant')
        self.assertEqual(self.delivery.reconcile(self.job, self.state)['action'], 'awaiting_receipt')
        with self.assertRaises(ValueError): self.delivery.prepare(self.job)
        with self.assertRaises(ValueError): self.delivery.archive(self.job, self.job['message'])

    def test_old_receipt_wrong_session_and_changed_message_are_rejected(self):
        self.user_message()
        self.delivery.prepare(self.job); self.delivery.begin_send(self.job)
        self.user_message('wrong-session', sessionId='other')
        self.user_message('changed', text='Messaggio diverso')
        self.assertEqual(self.delivery.reconcile(self.job, self.state)['action'], 'awaiting_receipt')

    def test_changed_draft_is_never_archived_or_cleared(self):
        self.delivery.prepare(self.job); self.delivery.begin_send(self.job); self.user_message()
        self.delivery.reconcile(self.job, self.state)
        with self.assertRaises(ValueError): self.delivery.archive(self.job, 'Bozza personale')
        self.assertFalse((self.root / 'logs/preserved-drafts').exists())

    def test_confirmation_survives_restart_without_duplicate_event(self):
        self.delivery.prepare(self.job); self.delivery.begin_send(self.job); self.user_message()
        self.delivery.reconcile(self.job, self.state)
        restarted = Delivery(self.store, self.root)
        self.assertIsNone(restarted.reconcile(self.job, self.state))
        self.assertTrue(restarted.cleanup_needed(self.job))
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM message_events WHERE event='Consegnato con ricevuta'").fetchone()[0], 1)

    def test_legacy_adoption_requires_prepare_reserve_send_and_keeps_deadline(self):
        at = time.time()
        self.store.update(self.job, 'drafted', 'prepared')
        self.store.update(self.job, 'uncertain', 'sending')
        self.user_message()
        self.store.update(self.job, 'idle', 'sent', due=at + 900, sent=at)
        self.assertEqual(self.delivery.reconcile(self.job, self.store.prepare(self.job, at))['action'], 'receipt_confirmed')
        self.assertEqual(self.store.rows()[0]['due'], at + 900)
        self.assertEqual(self.store.rows()[0]['last_sent'], at)

    def test_missing_legacy_receipt_keeps_existing_draft_foreign(self):
        self.store.update(self.job, 'drafted', 'prepared'); self.store.update(self.job, 'uncertain', 'sending')
        self.store.update(self.job, 'idle', 'sent', due=1900, sent=1000)
        self.assertIsNone(self.delivery.reconcile(self.job, self.store.rows()[0]))
        self.assertFalse(self.delivery.cleanup_needed(self.job))

    def test_native_cleanup_saves_before_clear_and_preserves_modified_draft(self):
        edit = Mock(Name='Prompt', ControlTypeName='EditControl', AriaRole='', IsEnabled=True)
        pattern = SimpleNamespace(Value=self.job['message'], SetValue=Mock())
        pattern.SetValue.side_effect = lambda value, **kwargs: setattr(pattern, 'Value', value)
        edit.GetValuePattern.return_value = pattern
        row = ({'title': 'Test', 'group': 'Project', 'ambiguous': False}, Mock())
        saved = Mock()
        with patch('chat_desktop.action_block_reason', return_value=None), \
             patch('chat_desktop.snapshot', return_value=([edit], 'Test')), \
             patch('chat_desktop.entries', return_value=[row]), \
             patch('chat_desktop.question_card', return_value=None):
            result = step({}, self.job, {'cleanup': True, 'archive': saved}, live_guard=lambda: None)
            self.assertEqual(result['action'], 'draft_cleared')
            saved.assert_called_once_with(self.job['message'])
            pattern.SetValue.assert_called_once_with('', waitTime=0)
            pattern.Value = 'My draft'; pattern.SetValue.reset_mock(); saved.reset_mock()
            result = step({}, self.job, {'cleanup': True, 'archive': saved}, live_guard=lambda: None)
            self.assertEqual(result['action'], 'draft_preserved'); saved.assert_not_called(); pattern.SetValue.assert_not_called()

    def test_live_engine_reserves_before_native_draft_and_waits_for_receipt(self):
        button = Mock()
        def adapter(config, job, delivery, **kwargs):
            if not delivery.get('drafted'):
                delivery['own_draft']()
                self.assertEqual(self.delivery.current(job)['stage'], 'prepared')
                return {'action': 'drafted'}
            return {'action': 'ready', 'button': button}
        with patch('message_engine.ROOT', self.root), patch('message_engine.load_jobs', return_value=[self.job]), \
             patch('chat_desktop.step', side_effect=adapter), patch('automation_policy.action_block_reason', return_value=None), \
             patch('automation_policy.live_block_reason', return_value=None), \
             patch('automation_policy.native_delivery_config', return_value={}):
            self.assertEqual(tick({}, self.store)['action'], 'drafted')
            self.assertEqual(tick({}, self.store)['action'], 'awaiting_receipt')
            self.assertEqual(self.store.rows()[0]['phase'], 'uncertain')
            self.assertEqual(tick({}, self.store)['action'], 'awaiting_receipt')
            button.GetInvokePattern.return_value.Invoke.assert_called_once()

    def test_readonly_receipt_confirmation_never_opens_or_changes_the_composer(self):
        self.delivery.prepare(self.job); self.delivery.begin_send(self.job); self.user_message()
        self.store.update(self.job, 'uncertain', 'reserved')
        with patch('message_engine.ROOT', self.root), patch('message_engine.load_jobs', return_value=[self.job]), \
             patch('chat_desktop.step') as native:
            self.assertEqual(tick({}, self.store, reconcile_only=True)['action'], 'receipt_confirmed')
            native.assert_not_called()
            self.assertEqual(self.delivery.current(self.job)['stage'], 'confirmed')

    def test_claude_background_policy_does_not_override_foreground_or_pause(self):
        from codex_local_sender import native_delivery_config
        config = {'paused': False, 'idle_seconds_before_actions': 900,
                  'apps': {'claude': {'process_path': 'C:/Claude/claude.exe'}}}
        with patch('automation_policy.idle_seconds', return_value=0):
            background = native_delivery_config(lambda: config, foreground_exe='C:/Editor/editor.exe', app='claude')
            self.assertTrue(background['native_background_delivery'])
            self.assertEqual(config['idle_seconds_before_actions'], 900)
            self.assertNotIn('native_background_delivery', native_delivery_config(lambda: config, foreground_exe='C:/Editor/editor.exe', app='claude', allow_present=False))
            self.assertNotIn('native_background_delivery', native_delivery_config(lambda: config, foreground_exe='C:/Claude/claude.exe', app='claude'))
            config['paused'] = True
            self.assertNotIn('native_background_delivery', native_delivery_config(lambda: config, foreground_exe='C:/Editor/editor.exe', app='claude'))

    def test_receipt_for_a_changed_target_cannot_clear_a_draft(self):
        self.delivery.prepare(self.job); self.delivery.begin_send(self.job); self.user_message()
        self.delivery.reconcile(self.job, self.state)
        with patch('claude_delivery.locate', return_value={**self.identity, 'session': 'different'}):
            with self.assertRaises(ValueError): self.delivery.archive(self.job, self.job['message'])
        self.assertFalse((self.root / 'logs/preserved-drafts').exists())

    def test_proven_modified_submission_consumes_only_its_slot_without_confirmation(self):
        self.delivery.prepare(self.job); self.delivery.begin_send(self.job)
        row = self.delivery.current(self.job)
        actual = self.job['message'] + 'v'
        for operation in ['enqueue', 'dequeue']:
            with self.history.open('a', encoding='utf-8') as f:
                f.write(json.dumps({'type': 'queue-operation', 'operation': operation,
                                    'sessionId': 'session', 'timestamp': datetime.now(timezone.utc).isoformat(),
                                    'content': actual if operation == 'enqueue' else None}) + '\n')
        self.user_message('edited', text=actual)
        self.store.update(self.job, 'uncertain', 'reserved')
        self.assertEqual(self.delivery.reconcile(self.job, self.state)['action'], 'modified_submission')
        state = self.store.rows()[0]
        self.assertIsNone(state['last_sent'])
        self.assertEqual(state['due'], row['send_started'] + 900)
        self.assertFalse(self.delivery.cleanup_needed(self.job))
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM message_events WHERE event='Consegnato con ricevuta'").fetchone()[0], 0)

    def test_changed_user_message_without_matching_queue_keeps_reservation_pending(self):
        self.delivery.prepare(self.job); self.delivery.begin_send(self.job)
        self.user_message('edited', text=self.job['message'] + 'v')
        self.assertEqual(self.delivery.reconcile(self.job, self.state)['action'], 'awaiting_receipt')

    def test_second_native_validation_preserves_user_edit_without_pressing_send(self):
        self.delivery.prepare(self.job)
        self.store.update(self.job, 'drafted', 'prepared')
        button = Mock()
        with patch('message_engine.ROOT', self.root), patch('message_engine.load_jobs', return_value=[self.job]), \
             patch('chat_desktop.step', return_value={'action': 'ready', 'button': button, 'validate_draft': Mock(side_effect=[True, False])}), \
             patch('automation_policy.action_block_reason', return_value=None), \
             patch('automation_policy.native_delivery_config', return_value={}):
            self.assertEqual(tick({}, self.store)['action'], 'draft_preserved')
            button.GetInvokePattern.assert_not_called()
            self.assertEqual(self.delivery.current(self.job)['stage'], 'draft_preserved')

    def test_preflight_failure_before_invoke_recovers_owned_draft_without_new_reservation(self):
        self.delivery.prepare(self.job)
        token = self.delivery.current(self.job)['token']
        self.store.update(self.job, 'uncertain', 'preflight')
        self.assertEqual(self.delivery.reconcile(self.job, self.store.rows()[0])['action'], 'draft_recovered')
        self.assertEqual(self.store.rows()[0]['phase'], 'drafted')
        self.assertEqual(self.delivery.current(self.job)['token'], token)

    def test_session_identity_survives_a_worktree_directory_change(self):
        sid = 'f2fd7e10-307d-4a99-8492-88519e698231'
        index = self.root / 'Local/Claude-Data/claude_test/claude-code-sessions/a/b/index.json'
        index.parent.mkdir(parents=True)
        index.write_text(json.dumps({'title': 'Test', 'cwd': 'C:/Work/AnotherWorktree', 'originCwd': 'C:/Work/AnotherWorktree',
                                     'isArchived': False, 'lastActivityAt': 1, 'cliSessionId': sid}), encoding='utf-8')
        history = self.root / '.claude/projects/project' / (sid + '.jsonl')
        history.parent.mkdir(parents=True); history.write_text('', encoding='utf-8')
        with patch.dict(os.environ, {'LOCALAPPDATA': str(self.root / 'Local'), 'APPDATA': str(self.root / 'Roaming')}), \
             patch('claude_delivery.Path.home', return_value=self.root):
            self.assertEqual(locate(self.job)['session'], sid)
            duplicate = index.with_name('duplicate.json'); duplicate.write_bytes(index.read_bytes())
            with self.assertRaises(ValueError):locate(self.job)

    def test_same_session_history_can_move_between_worktrees_before_and_after_invoke(self):
        self.delivery.prepare(self.job)
        moved = self.root / 'moved.jsonl'; moved.write_text('', encoding='utf-8')
        identity = {**self.identity, 'history': str(moved)}
        with patch('claude_delivery.locate', return_value=identity):
            self.delivery.begin_send(self.job)
            self.assertEqual(json.loads(self.delivery.current(self.job)['identity'])['history'], str(moved))
        self.user_message()
        self.assertEqual(self.delivery.reconcile(self.job, self.state)['action'], 'receipt_confirmed')

    def test_separate_system_reminders_do_not_hide_the_exact_human_text_block(self):
        self.delivery.prepare(self.job); self.delivery.begin_send(self.job)
        record={'type':'user','sessionId':'session','uuid':'with-reminder',
                'timestamp':datetime.now(timezone.utc).isoformat(),
                'message':{'role':'user','content':[{'type':'text','text':'<system-reminder>Runtime status, not user text.</system-reminder>'},
                                                 {'type':'text','text':self.job['message'].rstrip(' ')}]}}
        with self.history.open('a',encoding='utf-8') as f:f.write(json.dumps(record)+'\n')
        result=self.delivery.reconcile(self.job,self.state)
        self.assertEqual(result['action'],'receipt_confirmed')
        self.assertEqual(result['receipt']['text'],self.job['message'].rstrip(' '))

    def test_arbitrary_wrapped_or_assistant_mention_is_not_a_receipt(self):
        self.delivery.prepare(self.job);self.delivery.begin_send(self.job)
        self.user_message('quotation',text='Someone said: '+self.job['message'])
        self.assertEqual(self.delivery.reconcile(self.job,self.state)['action'],'awaiting_receipt')

    def test_uninvoked_preparation_can_be_reused_but_queued_text_cannot_be_sent_twice(self):
        self.delivery.prepare(self.job);token=self.delivery.current(self.job)['token']
        self.delivery.prepare(self.job)
        self.assertEqual(self.delivery.current(self.job)['token'],token)
        with self.history.open('a',encoding='utf-8') as f:
            f.write(json.dumps({'type':'queue-operation','operation':'enqueue','sessionId':'session',
                                'timestamp':datetime.now(timezone.utc).isoformat(),'content':self.job['message']})+'\n')
        self.assertEqual(self.delivery.reconcile(self.job,self.state)['action'],'awaiting_receipt')

    def test_manually_submitted_owned_draft_is_consumed_without_fabricating_invoke(self):
        self.delivery.prepare(self.job);self.user_message('manual')
        result=self.delivery.reconcile(self.job,self.state)
        self.assertEqual(result['action'],'receipt_confirmed')
        self.assertIsNone(self.delivery.current(self.job)['send_started'])
        self.assertEqual(result['receipt']['origin'],'owned_draft_submitted_outside_sender')


if __name__ == '__main__':
    unittest.main()
