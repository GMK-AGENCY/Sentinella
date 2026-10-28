"""Private save/discard checks. All dialogs are mocked or kept withdrawn."""
import copy
import json
import os
from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import Mock, patch

import automation_policy
import approvals_panel
from config_store import ensure_local_files
from default_message_templates import LEGACY_TEMPLATE_TEXTS_BY_ID, starter_library
import message_library
import settings_panel
import unsaved_changes
import workspace_ui


class UnsavedDecisionTests(unittest.TestCase):
    def test_clean_page_has_no_dialog_or_side_effect(self):
        save, discard = Mock(), Mock()
        with patch('unsaved_changes._choose_unsaved') as dialog:
            self.assertTrue(unsaved_changes.resolve_unsaved(None, dirty=False, save=save, discard=discard))
        dialog.assert_not_called(); save.assert_not_called(); discard.assert_not_called()

    def test_save_success_and_validation_failure_control_navigation(self):
        for result in (True, False):
            with self.subTest(result=result), patch('unsaved_changes._choose_unsaved', return_value='save'):
                save, discard = Mock(return_value=result), Mock()
                self.assertEqual(unsaved_changes.resolve_unsaved(None, dirty=True, save=save, discard=discard), result)
                save.assert_called_once_with(); discard.assert_not_called()

    def test_discard_reloads_before_allowing_navigation_and_can_fail(self):
        for result, expected in ((None, True), (True, True), (False, False)):
            with self.subTest(result=result), patch('unsaved_changes._choose_unsaved', return_value='discard'):
                save, discard = Mock(), Mock(return_value=result)
                self.assertEqual(unsaved_changes.resolve_unsaved(None, dirty=True, save=save, discard=discard), expected)
                discard.assert_called_once_with(); save.assert_not_called()

    def test_window_close_cancels_without_saving_or_discarding(self):
        with patch('unsaved_changes._choose_unsaved', return_value=None):
            save, discard = Mock(), Mock()
            self.assertFalse(unsaved_changes.resolve_unsaved(None, dirty=True, save=save, discard=discard))
        save.assert_not_called(); discard.assert_not_called()


class UntouchedSeedUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / 'message-library.json'
        self.store = message_library.MessageLibraryStore(self.path)
        self.current = starter_library()

    def legacy_data(self):
        data = copy.deepcopy(self.current)
        for item in data['items']:
            item['text'] = LEGACY_TEMPLATE_TEXTS_BY_ID[item['id']]['text']
        return data

    def write(self, data):
        self.path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        return self.path.read_bytes()

    def test_all_exact_legacy_seeds_upgrade_only_in_memory(self):
        data = self.legacy_data()
        before = self.write(data)
        actual = {item['id']: item for item in self.store.list()}
        for expected in self.current['items']:
            self.assertEqual(actual[expected['id']], expected)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(self.store.backup.exists())

    def test_each_customized_field_prevents_legacy_replacement(self):
        for field, value in (('title', 'Titolo personale'), ('text', 'Testo personale'),
                             ('text', None), ('updated_at', 1), ('id', '11111111-1111-4111-8111-111111111111')):
            with self.subTest(field=field, value=value):
                data = self.legacy_data()
                item = data['items'][0]
                item[field] = item['text'] + '\n' if field == 'text' and value is None else value
                before = self.write(data)
                actual = self.store.get(item['id'])
                self.assertEqual(actual, item)
                self.assertEqual(self.path.read_bytes(), before)

    def test_zero_float_is_untouched_but_boolean_timestamp_is_invalid(self):
        data = self.legacy_data()
        data['items'][0]['updated_at'] = 0.0
        self.write(data)
        expected = self.current['items'][0]
        self.assertEqual(self.store.get(expected['id'])['text'], expected['text'])
        data['items'][0]['updated_at'] = False
        before = self.write(data)
        with self.assertRaises(ValueError):
            self.store.list()
        self.assertEqual(self.path.read_bytes(), before)

    def test_saving_unrelated_item_does_not_persist_other_seed_overlays(self):
        data = self.legacy_data()
        self.write(data)
        self.store.list()
        self.store.save('Personale', 'Testo personale')
        actual = json.loads(self.path.read_text(encoding='utf-8'))
        for legacy in data['items']:
            self.assertEqual(next(item for item in actual['items'] if item['id'] == legacy['id']), legacy)


class PanelUnsavedTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='SentinellaUnsavedQA_')
        self.addCleanup(self.temporary.cleanup)
        self.profile = Path(self.temporary.name)
        patches = [
            patch.dict(os.environ, {'SENTINELLA_DATA_DIR': str(self.profile)}),
            patch('message_library.data_root', return_value=self.profile),
            patch.object(automation_policy, 'ROOT', self.profile),
            patch.object(settings_panel, 'ROOT', self.profile),
            patch.object(approvals_panel, 'ROOT', self.profile),
            patch.object(workspace_ui, 'ROOT', self.profile),
            patch('settings_panel.startup_enabled', return_value=False),
            patch('workspace_ui.startup_enabled', return_value=False),
            patch('unsaved_changes._choose_unsaved', return_value=None),
        ]
        self.mocks = [item.start() for item in patches]
        self.decision = self.mocks[-1]
        self.choice_patch = patches[-1]
        for item in patches:
            self.addCleanup(item.stop)
        ensure_local_files(self.profile)
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.close_root)
        self.library = message_library.MessageLibraryPanel(self.root)
        self.settings = settings_panel.SettingsPanel(self.root, Mock(), Mock(), Mock())

    def close_root(self):
        for callback in self.root.tk.call('after', 'info'):
            self.root.tk.call('after', 'cancel', callback)
        self.root.destroy()

    def saved_item(self):
        item = self.library.store.save('Messaggio QA', 'Testo salvato')
        self.library.refresh(guard=False)
        self.library._load(self.library.store.get(item['id']))
        self.library.tree.selection_set(item['id'])
        return item

    def test_library_discard_restores_persisted_title_text_and_collection(self):
        item = self.saved_item()
        before = self.library.store.path.read_bytes()
        self.library.title_value.set('Titolo temporaneo')
        self.library.text.insert('end', ' WIP')
        self.library.collection_value.set('Qualità')
        self.assertTrue(self.library.is_dirty())
        self.decision.return_value = 'discard'
        self.assertTrue(self.library.may_leave())
        self.assertEqual(self.library.title_value.get(), item['title'])
        self.assertEqual(self.library.text.get('1.0', 'end-1c'), item['text'])
        self.assertEqual(self.library.collection_options[self.library.collection_value.get()], item['collection_id'])
        self.assertFalse(self.library.is_dirty())
        self.assertEqual(self.library.store.path.read_bytes(), before)

    def test_library_cancel_selection_restores_old_selection_and_draft(self):
        item = self.saved_item()
        other = self.library.items[0]
        if other['id'] == item['id']:
            other = self.library.items[1]
        self.library.text.insert('end', ' WIP')
        self.library.tree.selection_set(other['id'])
        self.library.select()
        self.assertEqual(self.library.selected_id, item['id'])
        self.assertEqual(self.library.tree.selection(), (item['id'],))
        self.assertIn('WIP', self.library.text.get('1.0', 'end-1c'))

    def test_library_invalid_save_prevents_leave_and_retains_work(self):
        self.saved_item()
        before = self.library.store.path.read_bytes()
        self.library.title_value.set('')
        self.decision.return_value = 'save'
        self.assertFalse(self.library.may_leave())
        self.assertTrue(self.library.title_error.get())
        self.assertEqual(self.library.store.path.read_bytes(), before)
        self.assertTrue(self.library.is_dirty())

    def test_library_reload_guard_and_discard_read_latest_persisted_record(self):
        item = self.saved_item()
        self.library.text.insert('end', ' WIP')
        self.library.store.save(item['title'], 'Aggiornamento esterno', item['id'])
        self.assertFalse(self.library.refresh())
        self.assertIn('WIP', self.library.text.get('1.0', 'end-1c'))
        self.decision.return_value = 'discard'
        self.assertTrue(self.library.may_leave())
        self.assertEqual(self.library.text.get('1.0', 'end-1c'), 'Aggiornamento esterno')

    def test_rename_collection_does_not_restore_a_discarded_collection_move(self):
        item = self.saved_item()
        original_collection = self.library.collection_value.get()
        self.library.collection_value.set('Qualità')
        self.library.collection_filter.set('Riepiloghi')
        self.decision.return_value = 'discard'
        actual_top = tk.Toplevel
        opened = []
        def hidden_dialog(parent):
            window = actual_top(parent)
            window.withdraw()
            opened.append(window)
            return window
        with patch('message_library.tk.Toplevel', side_effect=hidden_dialog):
            self.library.collection_dialog(rename=True)
        window = opened[0]
        widgets = []
        def walk(widget):
            for child in widget.winfo_children():
                widgets.append(child)
                walk(child)
        walk(window)
        entry = next(child for child in widgets if isinstance(child, ttk.Entry))
        entry.delete(0, 'end'); entry.insert(0, 'Riepiloghi mensili')
        next(child for child in widgets if isinstance(child, ttk.Button) and child.cget('text') == 'Salva raccolta').invoke()
        self.assertEqual(self.library.collection_value.get(), original_collection)
        self.assertEqual(self.library.store.get(item['id'])['collection_id'], item['collection_id'])
        self.assertFalse(self.library.is_dirty())

    def test_settings_immediate_switch_does_not_save_dirty_timing(self):
        before = automation_policy.load_config()
        self.settings.notifications.set('61')
        self.settings.idle.set('3')
        self.settings.away.set(not before['away_mode'])
        self.assertTrue(self.settings.save_away())
        after = automation_policy.load_config()
        self.assertEqual(after['notification_poll_seconds'], before['notification_poll_seconds'])
        self.assertEqual(after['idle_seconds_before_actions'], before['idle_seconds_before_actions'])
        self.assertEqual(after['away_mode'], not before['away_mode'])
        self.assertTrue(self.settings.is_dirty())
        self.decision.return_value = 'discard'
        self.assertTrue(self.settings.may_leave())
        self.assertFalse(self.settings.is_dirty())
        self.assertEqual(automation_policy.load_config()['away_mode'], not before['away_mode'])

    def test_settings_save_and_invalid_save_have_explicit_navigation_result(self):
        self.settings.notifications.set('60')
        self.decision.return_value = 'save'
        self.assertTrue(self.settings.may_leave())
        self.assertEqual(automation_policy.load_config()['notification_poll_seconds'], 60)
        self.assertFalse(self.settings.is_dirty())
        before = (self.profile / 'approval-config.json').read_bytes()
        self.settings.idle.set('not-a-number')
        self.assertFalse(self.settings.may_leave())
        self.assertEqual((self.profile / 'approval-config.json').read_bytes(), before)
        self.assertTrue(self.settings.feedback.get())

    def test_failed_immediate_switch_restores_saved_value_without_losing_timing_draft(self):
        original = automation_policy.load_config()['away_mode']
        before = (self.profile / 'approval-config.json').read_bytes()
        self.settings.notifications.set('61')
        self.settings.away.set(not original)
        with patch('sentinel_launcher.atomic_json', side_effect=OSError('QA disk failure')):
            self.assertFalse(self.settings.save_away())
        self.assertEqual(self.settings.away.get(), original)
        self.assertEqual(self.settings.notifications.get(), '61')
        self.assertTrue(self.settings.is_dirty())
        self.assertIn('QA disk failure', self.settings.feedback.get())
        self.assertEqual((self.profile / 'approval-config.json').read_bytes(), before)
        self.settings.on_saved.assert_not_called()

    def test_numeric_equivalent_timings_and_immediate_switches_are_not_dirty(self):
        self.settings.notifications.set(self.settings.notifications.get() + '.0')
        self.settings.away.set(not self.settings.away.get())
        self.assertFalse(self.settings.is_dirty())
        self.assertTrue(self.settings.may_leave())
        self.decision.assert_not_called()

    def test_unsaved_dialog_has_two_actions_and_destroys_before_save_callback(self):
        actual_top = tk.Toplevel
        windows = []

        def hidden_dialog(parent):
            window = actual_top(parent)
            window.withdraw()
            window.deiconify = Mock()
            window.grab_set = Mock()
            windows.append(window)

            def finish():
                buttons = []
                def walk(widget):
                    for child in widget.winfo_children():
                        if isinstance(child, ttk.Button):
                            buttons.append(child)
                        walk(child)
                walk(window)
                self.assertEqual({button.cget('text') for button in buttons}, {'Scarta modifiche', 'Salva modifiche'})
                next(button for button in buttons if button.cget('text') == 'Salva modifiche').invoke()
            window.wait_window = finish
            return window

        def save():
            self.assertFalse(windows[0].winfo_exists())
            return True

        self.choice_patch.stop()
        with patch('unsaved_changes.tk.Toplevel', side_effect=hidden_dialog):
            self.assertTrue(unsaved_changes.resolve_unsaved(self.library, dirty=True, save=save, discard=Mock()))

    def test_export_dialog_saves_library_only_and_keeps_invalid_name_open(self):
        actual_top = tk.Toplevel
        opened = []
        def hidden_dialog(parent):
            window = actual_top(parent)
            window.withdraw()
            opened.append(window)
            return window
        original_config = (self.profile / 'messages-config.json').read_bytes()
        literal = 'È letterale\n  spazi\n'
        saved = Mock()
        with patch('message_library.tk.Toplevel', side_effect=hidden_dialog):
            window = message_library.save_text_as_message(self.library, literal, saved)
        widgets = []
        def walk(widget):
            for child in widget.winfo_children():
                widgets.append(child)
                walk(child)
        walk(window)
        save_button = next(child for child in widgets if isinstance(child, ttk.Button) and child.cget('text') == 'Salva messaggio')
        save_button.invoke()
        self.assertTrue(window.winfo_exists())
        self.assertFalse(self.library.store.path.exists())
        entry = next(child for child in widgets if isinstance(child, ttk.Entry))
        entry.insert(0, 'Testo esportato')
        save_button.invoke()
        self.assertFalse(window.winfo_exists())
        saved.assert_called_once()
        self.assertEqual(saved.call_args.args[0]['text'], literal)
        self.assertEqual((self.profile / 'messages-config.json').read_bytes(), original_config)


if __name__ == '__main__':
    unittest.main()
