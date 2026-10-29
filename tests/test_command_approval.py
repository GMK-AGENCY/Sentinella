"""Command approval is explicit, revocable and one-time; no commands execute."""
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import approval_desktop as adapter
from automation_policy import command_approval_enabled, approval_actions_enabled
from config_store import default_config
from question_supervisor import scheduled_scan
from polling_policy import ScanClock
from settings_panel import SettingsPanel
from question_desktop import plain_claude_candidate


class Node:
    def __init__(self, name, kind='TextControl', parent=None, role=''):
        self.Name, self.ControlTypeName, self.parent, self.AriaRole = name, kind, parent, role
        self.IsEnabled, self.IsOffscreen = True, False
    def GetParentControl(self): return self.parent
    def GetRuntimeId(self): return [1, id(self)]


class CommandApprovalTests(unittest.TestCase):
    def setUp(self):
        self.config = default_config()
        self.config.update(paused=False, idle_seconds_before_actions=0)
        self.config['apps']['claude']['process_path'] = 'Claude.exe'
        self.dialog = Node('Richiesta di autorizzazione: eseguire questo comando', 'GroupControl')
        self.nodes = [self.dialog, Node('SIMULAZIONE: comando con cancellazione; non viene eseguito'),
                      Node('Consenti sempre', 'ButtonControl'), Node('Consenti una volta', 'ButtonControl'), Node('Nega', 'ButtonControl')]

    def inspect(self, enabled=False):
        with patch.object(adapter, 'walk', return_value=self.nodes):
            return adapter.inspect_dialog(self.dialog, [], time.monotonic()+2, True, enabled)

    def test_default_and_existing_profiles_do_not_authorize_commands(self):
        self.assertFalse(default_config()['automatic_command_approval'])
        self.assertFalse(command_approval_enabled(self.config, 'claude'))
        result = self.inspect()
        self.assertTrue(result['summary']['manual_required'])
        self.assertNotIn('button', result)

    def test_opt_in_uses_only_once_even_with_persistent_file_preference(self):
        result = self.inspect(True)
        self.assertTrue(result['summary']['eligible'])
        self.assertEqual(result['button'].Name, 'Consenti una volta')
        self.assertEqual(result['summary']['request_type'], 'command')
        self.assertFalse(result['prefer_always'])

    def test_no_once_button_means_no_approval(self):
        self.nodes = [n for n in self.nodes if n.Name != 'Consenti una volta']
        self.assertNotIn('button', self.inspect(True))

    def test_quoted_command_does_not_promote_an_unknown_card(self):
        self.dialog.Name = 'Messaggio assistente'
        self.assertNotIn('button', self.inspect(True))

    def test_structured_command_form_uses_a_single_approval(self):
        self.dialog.Name = ''
        self.dialog.AriaRole = 'form'
        self.nodes[1:1] = [Node('Autorizzazioni'),Node('Consentire a ChatGPT di eseguire questo comando?')]
        self.assertEqual(self.inspect(True)['button'].Name, 'Consenti una volta')

    def test_codex_embedded_document_is_rejected(self):
        document = Node('App', 'DocumentControl')
        document.GetValuePattern = lambda: SimpleNamespace(Value='app://-/index.html')
        self.dialog.parent = document
        self.assertTrue(adapter.command_surface_verified(self.dialog, 'codex'))
        document.GetValuePattern = lambda: SimpleNamespace(Value='https://example.com')
        self.assertFalse(adapter.command_surface_verified(self.dialog, 'codex'))

    def test_file_toggle_independent_and_configured_app_required(self):
        self.config['automatic_command_approval'] = True
        self.assertFalse(self.config['enabled'])
        self.assertTrue(approval_actions_enabled(self.config, 'claude'))
        self.assertFalse(approval_actions_enabled(self.config, 'codex'))

    def test_revocation_blocks_a_prepared_click_before_touching_ui(self):
        record = self.inspect(True)
        with patch.object(adapter, 'live_block_reason', return_value=None), patch.object(adapter, 'load_config', return_value=self.config):
            self.assertFalse(adapter.invoke_record(record, 'claude'))

    def test_pause_blocks_prepared_click(self):
        with patch.object(adapter, 'live_block_reason', return_value='Sentinella in pausa globale'):
            self.assertFalse(adapter.invoke_record(self.inspect(True), 'claude'))

    def test_embedded_browser_is_not_a_native_chat(self):
        document = Node('Web preview', 'DocumentControl')
        document.GetValuePattern = lambda: SimpleNamespace(Value='https://example.com/consent')
        self.dialog.parent = document
        self.assertFalse(adapter.command_surface_verified(self.dialog, 'claude'))
        document.GetValuePattern = lambda: SimpleNamespace(Value='https://claude.ai/epitaxy/local_11111111-2222-4333-8444-555555555555')
        self.assertTrue(adapter.command_surface_verified(self.dialog, 'claude'))

    def test_pending_consent_does_not_trigger_an_absence_reply(self):
        self.nodes.insert(0, Node('Claude ha risposto: Posso procedere?', role='heading'))
        self.assertFalse(plain_claude_candidate(self.nodes, 'Chat', []))

    def test_command_only_scan_runs_without_file_or_reply_option(self):
        self.config['automatic_command_approval'] = True
        with patch('question_supervisor.run_adapter', return_value={'windows':1}), patch('question_supervisor.bounded_worker', return_value={'windows':1,'invoked':False}) as worker, patch('question_supervisor.log'):
            scheduled_scan(self.config, 'claude', ScanClock(), time.monotonic(), inspect_only=True)
            worker.assert_called_once_with('approval_desktop.py', '--app', 'claude', '--inspect-only')

    def test_setting_saves_only_opt_in_and_preserves_existing_values(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'approval-config.json'
            self.config.update(paused=True, idle_seconds_before_actions=300, allowed_project_roots=['C:/Delegated'])
            original = json.loads(json.dumps(self.config))
            path.write_text(json.dumps(self.config), encoding='utf-8')
            panel = SimpleNamespace(commands=Mock(get=Mock(return_value=True)), feedback=Mock(), on_saved=Mock())
            with patch('settings_panel.ROOT', Path(folder)), patch('settings_panel.load_config', return_value=self.config):
                self.assertTrue(SettingsPanel.save_commands(panel))
            saved=json.loads(path.read_text(encoding='utf-8'))
            self.assertTrue(saved.pop('automatic_command_approval'))
            original.pop('automatic_command_approval')
            self.assertEqual(saved, original)


if __name__ == '__main__': unittest.main()
