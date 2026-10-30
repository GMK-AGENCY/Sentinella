"""Question actions obey the absence toggle independently in both adapters."""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from question_desktop import inspect
from message_templates import (AWAY_REPLY, DEFAULT_MESSAGE, AUTONOMOUS_CONTINUATION,
                               PREVIOUS_AWAY_REPLY, PREVIOUS_DEFAULT_MESSAGE,
                               LEGACY_AWAY_REPLIES, LEGACY_PERIODIC_MESSAGES, reply_is_replaceable)


class QuestionReplyPolicyTests(unittest.TestCase):
    def test_default_replies_continue_authorized_work_and_defer_only_dependent_action(self):
        self.assertEqual(DEFAULT_MESSAGE,AUTONOMOUS_CONTINUATION)
        self.assertIn('per quando rientra e ti scrive',AWAY_REPLY)
        for reply in (AWAY_REPLY,DEFAULT_MESSAGE):
            with self.subTest(reply=reply):
                self.assertIn('utente è assente',reply)
                self.assertIn('fino a completare il lavoro già autorizzato',reply)
                self.assertIn('lascia sospesa soltanto l’attività che ne dipende',reply)
                self.assertIn('attività indipendenti già autorizzate',reply)
                self.assertIn('non aggirarli',reply)
                self.assertIn('non attribuirti nuove autorizzazioni',reply)
                self.assertIn('senza ampliare il perimetro',reply)
                self.assertIn('verifica i risultati',reply)
                for phrase in ('prima di procedere','proponi','attendi una risposta','domani'):
                    self.assertNotIn(phrase,reply.casefold())

    def test_historical_replies_remain_exact_candidates_without_replacing_custom_text(self):
        self.assertIn(PREVIOUS_AWAY_REPLY,LEGACY_AWAY_REPLIES)
        self.assertIn(PREVIOUS_DEFAULT_MESSAGE,LEGACY_PERIODIC_MESSAGES)
        self.assertNotEqual(PREVIOUS_AWAY_REPLY,AWAY_REPLY)
        self.assertNotEqual(PREVIOUS_DEFAULT_MESSAGE,DEFAULT_MESSAGE)
        config={'away_reply':AWAY_REPLY,'legacy_away_replies':list(LEGACY_AWAY_REPLIES)}
        self.assertTrue(reply_is_replaceable(PREVIOUS_AWAY_REPLY,config))
        self.assertFalse(reply_is_replaceable('Il mio testo personalizzato',config))

    def test_off_leaves_question_untouched_on_drafts_then_sends_only_once(self):
        for app in ('codex', 'claude'):
            with self.subTest(app=app), tempfile.TemporaryDirectory() as folder:
                document = Mock(ControlTypeName='DocumentControl', Name='Test question', AriaRole='')
                edit = Mock(ControlTypeName='EditControl', Name='Rispondi', AriaRole='')
                button = Mock(ControlTypeName='ButtonControl', Name='Invia', AriaRole='', IsEnabled=True)
                document.GetValuePattern.return_value.Value = ('app://-/index.html' if app=='codex' else
                    'https://claude.ai/epitaxy/local_11111111-2222-4333-8444-555555555555')
                document.GetRuntimeId.return_value=[1]
                edit.GetRuntimeId.return_value=[2];edit.GetParentControl.return_value=document
                button.GetRuntimeId.return_value=[3];button.GetParentControl.return_value=document
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
