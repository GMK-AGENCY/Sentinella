"""Question actions obey the absence toggle independently in both adapters."""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from question_desktop import inspect


class QuestionReplyPolicyTests(unittest.TestCase):
    def test_off_leaves_question_untouched_on_drafts_then_sends_only_once(self):
        for app in ('codex', 'claude'):
            with self.subTest(app=app), tempfile.TemporaryDirectory() as folder:
                document = Mock(ControlTypeName='DocumentControl', Name='Test question', AriaRole='')
                edit = Mock(ControlTypeName='EditControl', Name='Rispondi', AriaRole='')
                button = Mock(ControlTypeName='ButtonControl', Name='Invia', AriaRole='', IsEnabled=True)
                value = edit.GetValuePattern.return_value
                value.Value = ''
                card = {'edit': edit, 'send': button, 'text': 'Question?', 'runtime': [12, 34]}
                config = {'paused': False, 'away_mode': False, 'away_reply': 'Authorized absence reply',
                          'idle_seconds_before_actions': 0,
                          'apps': {app: {'process_path': 'test.exe', 'questions_enabled': True,
                                         'question_target_titles': ['Test question']}}}
                with patch('question_desktop.ROOT', Path(folder)), \
                     patch('question_desktop.desktop_is_unlocked', return_value=True), \
                     patch('question_desktop.app_window_handles', return_value=[1]), \
                     patch('question_desktop.native_controls', return_value=[document, edit, button]), \
                     patch('question_desktop.question_card', return_value=card), \
                     patch('question_desktop.live_block_reason', return_value=None), \
                     patch('uiautomation.ControlFromHandle', return_value=Mock()):
                    off = inspect(config, app, 'Test question', act=True)
                    self.assertEqual(off['action'], 'none')
                    value.SetValue.assert_not_called()
                    button.GetInvokePattern.assert_not_called()
                    self.assertFalse((Path(folder) / 'logs').exists())
                    config['away_mode'] = True
                    self.assertEqual(inspect(config, app, 'Test question', act=True)['action'], 'drafted_reply')
                    value.SetValue.assert_called_once_with(config['away_reply'], waitTime=0)
                    value.Value = config['away_reply']
                    self.assertEqual(inspect(config, app, 'Test question', act=True)['action'], 'sent_reply')
                    self.assertEqual(inspect(config, app, 'Test question', act=True)['action'], 'none')
                    button.GetInvokePattern.return_value.Invoke.assert_called_once()

    def test_question_scope_never_selects_an_excluded_chat(self):
        config = {'apps': {'claude': {'question_target_titles': ['Only test']}}}
        with patch('question_desktop.desktop_is_unlocked', return_value=True), \
             patch('question_desktop.app_window_handles') as windows:
            result = inspect(config, 'claude', 'Another chat', act=False)
        windows.assert_not_called()
        self.assertIn('esclusa', result['reason'])
