"""Stock Tk workflow QA. All catalogs, configuration and timers stay private.

No desktop adapter is launched, no chat is touched and no message is sent.
Run: .venv/Scripts/python.exe -m unittest discover -s tests -p test_workflow_qa.py -v
"""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import automation_policy
import approvals_panel
from config_store import ensure_local_files
import message_engine
import message_library
import messages_panel
import settings_panel
import workspace_ui


SOURCE = Path(__file__).resolve().parents[1]


class WorkflowQA(unittest.TestCase):
    def test_authorized_folder_removal_cancel_keeps_saved_configuration_and_list(self):
        panel=approvals_panel.ApprovalsPanel(self.root)
        panel.roots.insert('end','C:/Example/Project');panel.save();panel.roots.selection_set(0)
        before=(self.profile/'approval-config.json').read_bytes()
        with patch('approvals_panel.messagebox.askyesno',return_value=False) as confirm:
            self.assertFalse(panel.remove())
        self.assertEqual(confirm.call_args.kwargs['default'],'no')
        self.assertEqual(panel.roots.get(0,'end'),('C:/Example/Project',))
        self.assertEqual((self.profile/'approval-config.json').read_bytes(),before)
        panel.destroy()

    def test_authorized_folder_removal_confirms_and_rolls_back_on_failed_save(self):
        panel=approvals_panel.ApprovalsPanel(self.root)
        panel.roots.insert('end','C:/Example/Project');panel.save();panel.roots.selection_set(0)
        before=(self.profile/'approval-config.json').read_bytes()
        with patch('approvals_panel.messagebox.askyesno',return_value=True),patch.object(panel,'save',side_effect=OSError('QA failed save')):
            self.assertFalse(panel.remove())
        self.assertEqual(panel.roots.get(0,'end'),('C:/Example/Project',))
        self.assertEqual((self.profile/'approval-config.json').read_bytes(),before)
        with patch('approvals_panel.messagebox.askyesno',return_value=True):self.assertTrue(panel.remove())
        self.assertEqual(panel.roots.get(0,'end'),())
        self.assertEqual(automation_policy.load_config()['allowed_project_roots'],[])
        panel.destroy()

    def setUp(self):
        self.original_hashes = {
            name: hashlib.sha256((SOURCE / name).read_bytes()).hexdigest()
            for name in ("approval-config.json", "messages-config.json")
            if (SOURCE / name).exists()
        }
        self.temporary = tempfile.TemporaryDirectory(prefix="SentinellaWorkflowQA_")
        self.profile = Path(self.temporary.name)
        ensure_local_files(self.profile)
        self.patches = [
            patch.dict(os.environ, {"SENTINELLA_DATA_DIR": str(self.profile)}),
            patch.object(automation_policy, "ROOT", self.profile),
            patch.object(message_engine, "ROOT", self.profile),
            patch.object(message_engine, "CONFIG", self.profile / "messages-config.json"),
            patch.object(messages_panel, "ROOT", self.profile),
            patch.object(workspace_ui, "ROOT", self.profile),
            patch.object(settings_panel, "ROOT", self.profile),
            patch.object(approvals_panel, "ROOT", self.profile),
            patch("bounded_process.run_bounded", return_value=self.result([])),
        ]
        self.mocks = [p.start() for p in self.patches]
        self.native = self.mocks[-1]
        self.dialog_patch=patch('unsaved_changes._choose_unsaved',return_value=None)
        self.dialog_patch.start()
        self.addCleanup(self.dialog_patch.stop)
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.geometry("1040x790")
        self.callback_errors = []
        self.root.report_callback_exception = lambda *args: self.callback_errors.append(args)
        self.panel = messages_panel.MessagesPanel(self.root)
        self.panel.pack(fill="both", expand=True)
        self.row = {"app": "codex", "title": "QA Codex", "group": "QA Project",
                    "project": "QA Project", "state": "idle", "last_activity_at": time.time(),
                    "thread_id": "11111111-1111-4111-8111-111111111111"}
        self.other = {**self.row, "app": "claude", "title": "QA Claude", "thread_id": None}

    def tearDown(self):
        try:
            self.cancel_callbacks()
            self.root.destroy()
        finally:
            for patcher in reversed(self.patches):
                patcher.stop()
            self.temporary.cleanup()
        for name, expected in self.original_hashes.items():
            self.assertEqual(hashlib.sha256((SOURCE / name).read_bytes()).hexdigest(), expected,
                             f"Production file changed during isolated QA: {name}")

    def cancel_callbacks(self):
        # Each test owns its Tcl interpreter. Do not leave registered callbacks
        # behind after destroying its widgets, including manually polled ones.
        for callback in self.root.tk.call("after", "info"):
            # The callback may belong to a child widget. Let that widget delete
            # its Tcl command during destroy; only cancel the scheduled event.
            self.root.tk.call("after", "cancel", callback)

    @staticmethod
    def result(rows, **extra):
        return {"stdout": json.dumps({"chats": rows, **extra}), "stderr": "",
                "returncode": 0, "timed_out": False, "elapsed_seconds": .01}

    def finish_scan(self):
        until = time.monotonic() + 2
        while self.panel.results.empty() and time.monotonic() < until:
            time.sleep(.005)
        self.assertFalse(self.panel.results.empty(), "Catalog worker never produced a terminal result")
        self.panel.poll()

    def seed(self, enabled=False):
        job = {**self.row, "id": "qa-job", "version": "original-version", "enabled": enabled,
               "activated_at": time.time() - 100, "interval_seconds": 900,
               "message": "Testo originale", "repeat": True, "transport": "codex_app",
               "send_when_active": False, "send_when_present": False}
        message_engine.save_jobs([job])
        store = message_engine.TimerStore(self.profile / "logs/messages.sqlite3")
        state = store.prepare(job, time.time())
        store.db.close()
        self.panel.render_jobs()
        self.panel.job_list.selection_set(job["id"])
        return job, state

    def test_catalog_with_dates_finishes_and_reenables_refresh(self):
        self.native.side_effect = [self.result([self.row]), self.result([self.other])]
        self.panel.new()
        self.finish_scan()
        self.assertEqual(len(self.panel.chat_list.get_children()), 2)
        self.assertFalse(self.panel.scan_running)
        self.assertNotIn("disabled", self.panel.refresh_button.state())
        self.assertEqual(self.native.call_count, 2)
        self.assertFalse(self.callback_errors)

    def test_timeout_and_corrupt_catalog_finish_with_retry_available(self):
        self.native.side_effect = [dict(self.result([]), timed_out=True),
                                   dict(self.result([]), stdout="not-json")]
        self.panel.refresh()
        self.finish_scan()
        self.assertFalse(self.panel.scan_running)
        self.assertNotIn("disabled", self.panel.refresh_button.state())
        self.assertTrue(self.panel.scan_errors)
        self.native.side_effect = None
        self.native.return_value = self.result([self.row])
        self.panel.refresh()
        self.finish_scan()
        self.assertTrue(self.panel.chat_list.get_children())

    def test_catalog_render_error_does_not_disable_retry_or_next_poll(self):
        self.panel.scan_running = True
        self.panel.refresh_button.state(["disabled"])
        self.panel.results.put(([self.row], []))
        with patch.object(self.panel, "apply_filters", side_effect=RuntimeError("QA render error")):
            self.panel.poll()
        self.assertFalse(self.panel.scan_running)
        self.assertNotIn("disabled", self.panel.refresh_button.state())
        self.assertIn("QA render error", self.panel.catalog_status.get())
        self.panel.results.put(([self.row], []))
        self.panel.poll()
        self.assertTrue(self.panel.chat_list.get_children())

    def test_async_catalog_success_and_error_preserve_step_two_save_error(self):
        self.panel.targets = [self.row]
        self.panel.update_target_summary()
        self.panel.show_editor()
        self.finish_scan()
        self.panel.next_step()
        self.panel.status.set("Errore salvataggio da preservare")
        self.panel.results.put(([self.row], []))
        self.panel.poll()
        self.assertEqual(self.panel.status.get(), "Errore salvataggio da preservare")
        self.assertIn("1 di 1 chat", self.panel.catalog_status.get())
        with patch.object(self.panel, "apply_filters", side_effect=RuntimeError("QA errore catalogo")):
            self.panel.results.put(([self.row], []))
            self.panel.poll()
        self.assertEqual(self.panel.status.get(), "Errore salvataggio da preservare")
        self.assertIn("QA errore catalogo", self.panel.catalog_status.get())
        self.assertFalse(self.panel.scan_running)
        self.assertNotIn("disabled", self.panel.refresh_button.state())

    def test_chat_selection_uses_table_and_compact_summary(self):
        self.panel.clear_targets()
        self.assertFalse(hasattr(self.panel, 'selected_targets'))
        self.assertTrue(self.panel.clear_targets_button.instate(["disabled"]))
        self.panel.targets = [self.row]
        self.panel.update_target_summary()
        self.assertIn(self.row['title'], self.panel.target_label.get())
        self.assertFalse(self.panel.clear_targets_button.instate(["disabled"]))
        self.assertFalse(self.panel.continue_button.instate(["disabled"]))
        self.panel.targets = [{**self.row, 'ambiguous': True}]
        self.panel.update_target_summary()
        self.assertTrue(self.panel.continue_button.instate(["disabled"]))
        self.panel.clear_targets()
        self.assertEqual(self.panel.targets, [])
        self.assertTrue(self.panel.clear_targets_button.instate(["disabled"]))

    def test_unexpected_catalog_shape_does_not_abandon_worker_result(self):
        self.native.side_effect = [dict(self.result([]), stdout='{"chats": null}'),
                                   self.result([self.other])]
        self.panel.refresh()
        self.finish_scan()
        self.assertFalse(self.panel.scan_running)
        self.assertNotIn("disabled", self.panel.refresh_button.state())
        self.assertTrue(self.panel.scan_errors)
        self.assertEqual(len(self.panel.chat_list.get_children()), 1)

    def test_selected_chats_survive_app_filters(self):
        self.panel.editor_visible=True
        self.panel.editor_step=1
        self.panel.catalog = [self.row, self.other]
        self.panel.apply_filters()
        self.panel.toggle_chat_row("0")
        self.panel.app_filter.set("Claude")
        self.panel.apply_filters()
        self.panel.toggle_chat_row("0")
        self.assertEqual({row["app"] for row in self.panel.targets}, {"codex", "claude"})
        self.panel.search.set("absent")
        self.panel.apply_filters()
        self.assertEqual(len(self.panel.targets), 2)

    def test_edit_paused_noop_preserves_identity_and_schedule(self):
        previous, original = self.seed(enabled=False)
        self.panel.edit_selected()
        self.assertFalse(self.panel.scan_running)
        self.native.assert_not_called()
        self.panel.activate()
        saved = message_engine.load_jobs()[0]
        self.assertFalse(saved["enabled"])
        for key in ("id", "version", "activated_at", "thread_id", "transport"):
            self.assertEqual(saved[key], previous[key], key)
        store = message_engine.TimerStore(self.profile / "logs/messages.sqlite3")
        self.assertEqual(store.rows()[0]["due"], original["due"])
        store.db.close()

    def test_edit_paused_changed_text_stays_paused_and_gets_new_version(self):
        previous, _ = self.seed(enabled=False)
        self.panel.edit_selected()
        self.assertFalse(self.panel.scan_running)
        self.native.assert_not_called()
        self.panel.message.delete("1.0", "end")
        self.panel.message.insert("1.0", "Testo cambiato")
        self.panel.activate()
        saved = message_engine.load_jobs()[0]
        self.assertFalse(saved["enabled"])
        self.assertEqual(saved["id"], previous["id"])
        self.assertNotEqual(saved["version"], previous["version"])
        self.assertEqual(saved["message"], "Testo cambiato")

    def test_pause_resume_preserves_uncertain_reservation(self):
        previous, _ = self.seed(enabled=True)
        store = message_engine.TimerStore(self.profile / "logs/messages.sqlite3")
        store.update(previous, "uncertain", "QA prenotazione incerta")
        store.db.close()
        self.panel.set_enabled(False)
        self.panel.set_enabled(True)
        saved = message_engine.load_jobs()[0]
        self.assertEqual(saved["version"], previous["version"])
        store = message_engine.TimerStore(self.profile / "logs/messages.sqlite3")
        self.assertEqual(store.rows()[0]["phase"], "uncertain")
        store.db.close()

    def test_cancel_new_keeps_work_in_progress_and_saved_pause(self):
        previous, _ = self.seed(enabled=False)
        self.panel.edit_selected()
        self.assertFalse(self.panel.scan_running)
        self.native.assert_not_called()
        self.panel.message.delete("1.0", "end")
        self.panel.message.insert("1.0", "Bozza non salvata")
        self.panel.minutes.set("31")
        with patch("unsaved_changes._choose_unsaved", return_value=None) as confirm:
            self.panel.new()
        self.assertTrue(confirm.called)
        self.assertEqual(self.panel.message.get("1.0", "end-1c"), "Bozza non salvata")
        self.assertEqual(self.panel.minutes.get(), "31")
        self.assertEqual(self.panel.selected_job, previous["id"])
        self.assertEqual(self.panel.targets[0]["title"], previous["title"])
        self.assertFalse(message_engine.load_jobs()[0]["enabled"])

    def test_cancel_open_another_schedule_keeps_work_in_progress(self):
        previous, _ = self.seed(enabled=False)
        second = {**previous, **self.other, "id": "second-job"}
        message_engine.save_jobs([previous, second])
        self.panel.render_jobs()
        self.panel.edit_selected()
        self.assertFalse(self.panel.scan_running)
        self.native.assert_not_called()
        self.panel.message.delete("1.0", "end")
        self.panel.message.insert("1.0", "Bozza da proteggere")
        self.panel.job_list.selection_set(second["id"])
        with patch("unsaved_changes._choose_unsaved", return_value=None) as confirm:
            self.panel.edit_selected()
        self.assertTrue(confirm.called)
        self.assertEqual(self.panel.selected_job, previous["id"])
        self.assertEqual(self.panel.message.get("1.0", "end-1c"), "Bozza da proteggere")

    def test_settings_shared_controls_and_intervals_preserve_pause_and_jobs(self):
        self.seed(enabled=True)
        before = (self.profile / "messages-config.json").read_bytes()
        config = settings_panel.save_settings("30", "15", False, "2")
        self.assertTrue(config["paused"])
        self.assertEqual(config["notification_poll_seconds"], 30)
        self.assertEqual(config["chat_scan_seconds"], 900)
        self.assertEqual(config["idle_seconds_before_actions"], 120)
        settings_panel.save_shared_controls(False, True, False)
        config = automation_policy.load_config()
        self.assertTrue(config["paused"])
        self.assertFalse(config["away_mode"])
        self.assertTrue(all(not row["questions_enabled"] and row["approvals_enabled"]
                            for row in config["apps"].values()))
        self.assertEqual((self.profile / "messages-config.json").read_bytes(), before)

    def test_invalid_settings_do_not_partially_save(self):
        before = (self.profile / "approval-config.json").read_bytes()
        with self.assertRaises(ValueError):
            settings_panel.save_settings("30", "15", True, "nan")
        self.assertEqual((self.profile / "approval-config.json").read_bytes(), before)

    def test_global_pause_toggle_does_not_change_selected_timer_enables(self):
        self.seed(enabled=True)
        before = (self.profile / "messages-config.json").read_bytes()
        import notification_watch
        app = SimpleNamespace(supervisor=None, refresh_dashboard=lambda *_: None,
                              log=lambda *_: None)
        with patch.object(notification_watch, "ROOT", self.profile):
            notification_watch.Monitor.toggle_automations(app)
            self.assertFalse(automation_policy.load_config()["paused"])
            notification_watch.Monitor.toggle_automations(app)
        self.assertTrue(automation_policy.load_config()["paused"])
        self.assertEqual((self.profile / "messages-config.json").read_bytes(), before)

    def test_stock_workspace_editor_and_library_controls_fit_minimum_size(self):
        # Internal Tk layout test: transparent disabled test window. This does
        # not inspect/click the user's desktop or assert screenshot appearance.
        self.cancel_callbacks()
        self.panel.destroy()
        self.root.attributes("-alpha", 0.0)
        self.root.attributes("-disabled", True)
        app = SimpleNamespace(root=self.root, rows={}, toggle_automations=lambda: None,
                              restart_reader=lambda: None, refresh_dashboard=lambda *_: None,
                              select=lambda *_: None, show_details=lambda *_: None)
        workspace_ui.build_workspace(app)
        self.panel = app.messages_panel
        self.panel.refresh = lambda: None
        self.panel.new()
        self.panel.targets = [self.row, self.other]
        self.panel.update_target_summary()
        self.root.deiconify()

        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)

        for dimensions in ("1040x790", "1280x850"):
            self.root.geometry(dimensions)
            for page, setup in ((self.panel, self.panel.show_pick),
                                (self.panel, self.panel.next_step),
                                (app.library_panel, lambda: None)):
                self.panel.editor_baseline=self.panel.editor_signature()
                app.notebook.select(page)
                setup()
                self.root.update()
                self.root.update_idletasks()
                for control in descendants(page):
                    if isinstance(control, ttk.Button) and control.winfo_ismapped():
                        x = control.winfo_rootx() - self.root.winfo_rootx()
                        y = control.winfo_rooty() - self.root.winfo_rooty()
                        self.assertGreaterEqual(x, 0, control.cget("text"))
                        self.assertGreaterEqual(y, 0, control.cget("text"))
                        self.assertLessEqual(x + control.winfo_width(), self.root.winfo_width(),
                                             (dimensions, control.cget("text")))
                        self.assertLessEqual(y + control.winfo_height(), self.root.winfo_height(),
                                             (dimensions, control.cget("text")))
        self.assertFalse(self.callback_errors)

    def test_library_use_copies_literal_text_without_schedule_mutation(self):
        (self.profile/'message-library.json').write_text('{"schema_version":1,"items":[]}',encoding='utf-8')
        previous, _ = self.seed(enabled=False)
        self.panel.targets = [self.row, self.other]
        before = (self.profile / "messages-config.json").read_bytes()
        panel = message_library.MessageLibraryPanel(self.root, self.panel.use_saved_message)
        panel.title_value.set("Modello QA")
        panel.text.insert("1.0", "È letterale\n  spazio\n")
        self.assertTrue(panel.save())
        self.assertEqual(len(panel.store.list()), 1)
        panel.use()
        self.assertEqual(self.panel.message.get("1.0", "end-1c"), "È letterale\n  spazio\n")
        self.assertEqual(len(self.panel.targets), 2)
        self.assertEqual((self.profile / "messages-config.json").read_bytes(), before)
        self.assertEqual(message_engine.load_jobs()[0]["version"], previous["version"])
        panel.destroy()

    def test_library_dirty_cancel_select_new_and_delete_are_safe(self):
        (self.profile/'message-library.json').write_text('{"schema_version":1,"items":[]}',encoding='utf-8')
        panel = message_library.MessageLibraryPanel(self.root, lambda _: True)
        first = panel.store.save("Uno", "Testo uno")
        second = panel.store.save("Due", "Testo due")
        panel.refresh()
        panel._load(first)
        panel.text.insert("end", " WIP")
        panel.tree.selection_set(second["id"])
        with patch("unsaved_changes._choose_unsaved", return_value=None):
            panel.select()
            panel.new()
        self.assertEqual(panel.selected_id, first["id"])
        self.assertIn("WIP", panel.text.get("1.0", "end-1c"))
        with patch("message_library.messagebox.askyesno", return_value=False):
            panel.remove()
        self.assertEqual(len(panel.store.list()), 2)
        with patch("message_library.messagebox.askyesno", return_value=True):
            panel.remove()
        self.assertEqual(len(panel.store.list()), 1)
        self.assertEqual(panel.store.list()[0]["id"], second["id"])
        panel.destroy()

    def test_new_editor_defaults_zero_background_flags_and_save_is_explicit(self):
        self.panel.new()
        self.finish_scan()
        self.assertFalse(self.panel.send_when_active.get())
        self.assertFalse(self.panel.send_when_present.get())
        self.assertEqual(message_engine.load_jobs(), [])
        self.panel.targets = [self.row, self.other]
        self.panel.update_target_summary()
        self.panel.next_step()
        self.root.update_idletasks()
        self.assertEqual(self.panel.editor_step, 2)
        self.assertFalse(self.panel.commit_button.instate(["disabled"]))
        self.assertEqual(message_engine.load_jobs(), [])
        self.panel.activate()
        saved = message_engine.load_jobs()
        self.assertEqual(len(saved), 2)
        self.assertTrue(all(not j["send_when_active"] and not j["send_when_present"] for j in saved))
        self.assertTrue(automation_policy.load_config()["paused"])


if __name__ == "__main__":
    unittest.main()
