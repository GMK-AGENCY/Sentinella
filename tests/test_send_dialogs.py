"""Native sender guards: inactive panels must not stall an owned draft."""
import json
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace as Node
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from datetime import datetime, timezone
import uuid

from chat_desktop import send_block_reason, step
from message_engine import TimerStore, tick
from question_desktop import question_card


def dialog(role='dialog', offscreen=False, properties='modal=true'):
    return Node(AriaRole=role, Name='Test panel', IsOffscreen=offscreen,
                AriaProperties=properties, ControlTypeName='GroupControl')


class SendDialogTests(unittest.TestCase):
    def test_hidden_dialog_and_explicit_nonmodal_panel_do_not_block(self):
        with patch('chat_desktop.question_card', return_value=None), \
             patch('chat_desktop.native_controls', return_value=[Node(ControlTypeName='TextControl',AriaRole='heading')]):
            self.assertIsNone(send_block_reason([dialog(offscreen=True)]))
            self.assertIsNone(send_block_reason([dialog(properties='label=Usage;modal=false')]))
            self.assertIsNone(send_block_reason([dialog(properties='readonly=true;hasactions=false')]))

    def test_visible_modal_alert_and_unknown_state_keep_sends_blocked(self):
        with patch('chat_desktop.question_card', return_value=None), \
             patch('chat_desktop.native_controls', return_value=[]):
            for panel in [dialog(), dialog(role='alertdialog', properties='modal=false'),
                          dialog(properties=''), dialog(properties='modal=false;modal=true'),
                          dialog(properties=r'description=Note\;modal=false')]:
                with self.subTest(properties=panel.AriaProperties, role=panel.AriaRole):
                    self.assertIn('Test panel', send_block_reason([panel]))
            stale = Mock(AriaRole='dialog', Name='Stale panel')
            type(stale).IsOffscreen = property(lambda _: (_ for _ in ()).throw(RuntimeError('stale')))
            self.assertIn('non verificabile', send_block_reason([stale]))

    def test_nonmodal_request_with_action_controls_is_not_an_informational_panel(self):
        for kind in ('ButtonControl','EditControl','ListControl','ListItemControl','WindowControl',
                     'CheckBoxControl','ComboBoxControl','RadioButtonControl','SliderControl',
                     'SpinnerControl','MenuItemControl','TabItemControl','TreeItemControl','DataGridControl'):
            with self.subTest(kind=kind), patch('chat_desktop.question_card',return_value=None), \
                 patch('chat_desktop.native_controls',return_value=[Node(ControlTypeName=kind,AriaRole='')]):
                self.assertIn('Test panel',send_block_reason([dialog(properties='modal=false')]))

    def test_real_usage_panel_with_credit_help_is_informational_but_approve_is_not(self):
        panel=dialog(properties='readonly=true;expanded=false;hasactions=false');panel.Name='Utilizzo'
        heading=Node(ControlTypeName='TextControl',AriaRole='heading',Name='Utilizzo')
        help_button=Node(ControlTypeName='ButtonControl',AriaRole='button',Name='Informazioni su questo credito')
        with patch('chat_desktop.question_card',return_value=None), \
             patch('chat_desktop.native_controls',return_value=[heading,help_button]):
            self.assertIsNone(send_block_reason([panel]))
            help_button.Name='Consenti'
            self.assertIn('Utilizzo',send_block_reason([panel]))

    def test_real_question_still_blocks_beside_an_informational_panel(self):
        with patch('chat_desktop.question_card', return_value={'text': 'Question'}):
            self.assertIn('domanda nella chat', send_block_reason([dialog(properties='modal=false')]))

    def test_historical_or_disabled_question_edit_is_not_an_active_question(self):
        for hidden, enabled in [(True, True), (False, False)]:
            edit = Node(ControlTypeName='EditControl', Name='Rispondi', IsOffscreen=hidden, IsEnabled=enabled)
            with patch('question_desktop.walk') as walk:
                self.assertIsNone(question_card([edit]))
                walk.assert_not_called()

    def test_two_periodic_deliveries_ignore_inactive_panels_but_preserve_foreign_draft(self):
        with tempfile.TemporaryDirectory() as folder, ExitStack() as cleanup:
            root = Path(folder); history = root/'history.jsonl'; history.write_text('')
            identity = {'history': str(history), 'session': 'test-session', 'index': 'index'}
            job = dict(id='job', version='v', app='claude', title='Exact chat', group='Tests',
                       session_id='fixture-ui-session',
                       message='Authorized periodic message', enabled=True, interval_seconds=1800,
                       activated_at=0, repeat=True, send_when_active=True, send_when_present=True)
            store = TimerStore(root/'messages.sqlite3')
            cleanup.callback(store.db.close)
            value = Node(Value='')
            value.SetValue = lambda text, **kwargs: setattr(value, 'Value', text)
            edit = Mock(ControlTypeName='EditControl', Name='Prompt', AriaRole='', IsEnabled=True,
                        IsOffscreen=False, HasKeyboardFocus=False)
            edit.GetValuePattern.return_value = value
            send = Mock(ControlTypeName='ButtonControl', Name='Invia', AriaRole='', IsEnabled=True)
            def received():
                record = dict(type='user', sessionId=identity['session'], uuid=str(uuid.uuid4()),
                              timestamp=datetime.now(timezone.utc).isoformat(),
                              message={'role':'user', 'content':value.Value})
                with history.open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps(record)+'\n')
                value.Value = ''
            send.GetInvokePattern.return_value.Invoke.side_effect = received
            panels = [dialog(offscreen=True), dialog(properties='modal=false')]
            nodes = [edit, send, *panels]
            with patch('message_engine.ROOT', root), patch('message_engine.load_jobs', return_value=[job]), \
                 patch('chat_identity.resolve',side_effect=lambda job:dict(job)), \
                 patch('chat_desktop.selected_matches',side_effect=lambda controls,title,job:title==job['title']), \
                 patch('claude_delivery.locate', return_value=identity), \
                 patch('automation_policy.native_delivery_config', return_value={}), \
                 patch('automation_policy.action_block_reason', return_value=None), \
                 patch('chat_desktop.action_block_reason', return_value=None), \
                 patch('chat_desktop.snapshot', return_value=(nodes, job['title'])), \
                 patch('chat_desktop.entries', return_value=[({**job,'ambiguous':False}, Mock())]), \
                 patch('chat_desktop.walk', return_value=[send]), \
                 patch('chat_desktop.native_controls',return_value=[Node(ControlTypeName='TextControl',AriaRole='heading')]), \
                 patch('chat_desktop.question_card', return_value=None), \
                 patch('chat_desktop.typing_keys_down', return_value=False):
                for cycle in range(2):
                    now = time.time()+cycle*7200
                    self.assertEqual(tick({}, store, now=now)['action'], 'drafted')
                    self.assertEqual(tick({}, store, now=now+1)['action'], 'awaiting_receipt')
                    self.assertEqual(tick({}, store, now=now+2)['action'], 'draft_cleared')
                self.assertEqual(send.GetInvokePattern.return_value.Invoke.call_count, 2)
                self.assertEqual(store.db.execute("SELECT count(*) FROM claude_timer_deliveries WHERE stage='cleared'").fetchone()[0], 2)
                value.Value = 'Personal draft'
                self.assertEqual(tick({}, store, now=time.time()+15000)['action'], 'none')
                self.assertEqual(value.Value, 'Personal draft')
                self.assertEqual(send.GetInvokePattern.return_value.Invoke.call_count, 2)

    def test_modal_appearing_before_invoke_invalidates_prepared_send(self):
        edit = Mock(ControlTypeName='EditControl', Name='Prompt', AriaRole='', IsEnabled=True,
                    HasKeyboardFocus=False)
        edit.GetValuePattern.return_value.Value = 'Authorized text'
        send = Mock(ControlTypeName='ButtonControl', Name='Invia', AriaRole='', IsEnabled=True)
        job = dict(app='claude', title='Exact chat', group='Tests', message='Authorized text',
                   send_when_active=True, send_when_present=True)
        with patch('chat_desktop.action_block_reason', return_value=None), \
             patch('chat_desktop.snapshot', side_effect=[([edit,send],job['title']),([edit,send,dialog()],job['title'])]), \
             patch('chat_desktop.entries', return_value=[({**job,'ambiguous':False},Mock())]), \
             patch('chat_desktop.question_card', return_value=None), \
             patch('chat_desktop.walk', return_value=[send]):
            result = step({},job,{'drafted':True},live_guard=lambda:None)
            self.assertEqual(result['action'],'ready')
            self.assertFalse(result['validate_draft']())
            send.GetInvokePattern.assert_not_called()
