"""Isolated Tk checks for editing saved schedules below their list.

All profiles and timers are temporary. Native adapters and modal confirmations
are mocked; no chat, desktop automation or personal profile is accessed.
"""
import os
from pathlib import Path
import tempfile
import time
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import automation_policy
import approvals_panel
from config_store import ensure_local_files
import message_engine
import messages_panel
import settings_panel
import workspace_ui


class InlineSchedulingQA(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="SentinellaInlineQA_")
        self.addCleanup(self.temporary.cleanup)
        self.profile = Path(self.temporary.name)
        self.patchers = [
            patch.dict(os.environ, {"SENTINELLA_DATA_DIR": str(self.profile)}),
            patch.object(automation_policy, "ROOT", self.profile),
            patch.object(message_engine, "ROOT", self.profile),
            patch.object(message_engine, "CONFIG", self.profile / "messages-config.json"),
            patch.object(messages_panel, "ROOT", self.profile),
            patch.object(settings_panel, "ROOT", self.profile),
            patch.object(workspace_ui, "ROOT", self.profile),
            patch.object(messages_panel, "action_block_reason", return_value=""),
            patch("bounded_process.run_bounded", side_effect=AssertionError("Native adapter forbidden in inline QA")),
            patch("messages_panel.messagebox.askyesno", return_value=False),
            patch("unsaved_changes._choose_unsaved", return_value=None),
            patch.object(approvals_panel, "ROOT", self.profile),
        ]
        self.mocks = [patcher.start() for patcher in self.patchers]
        self.native = self.mocks[8]
        self.confirm = self.mocks[9]
        self.unsaved = self.mocks[10]
        for patcher in self.patchers:
            self.addCleanup(patcher.stop)
        ensure_local_files(self.profile)
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.geometry("1040x790")
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.addCleanup(self.close_root)
        self.panel = messages_panel.MessagesPanel(self.root)
        self.panel.pack(fill="both", expand=True)
        self.cancel_callbacks()

    def close_root(self):
        self.cancel_callbacks()
        self.root.destroy()

    def cancel_callbacks(self):
        for callback in self.root.tk.call("after", "info"):
            self.root.tk.call("after", "cancel", callback)

    def flush_events(self):
        self.root.update()
        self.root.update_idletasks()
        self.assertFalse(self.errors, self.errors)

    def seed(self):
        now = time.time()
        first = {
            "id": "qa-inline-one", "version": "qa-version-one", "enabled": False,
            "app": "codex", "title": "QA riepilogo progetto", "group": "QA project",
            "thread_id": "11111111-1111-4111-8111-111111111111", "transport": "codex_app",
            "activated_at": now - 100, "interval_seconds": 900, "message": "Testo originale\n",
            "repeat": True, "send_when_active": False, "send_when_present": False,
        }
        second = {
            **first, "id": "qa-inline-two", "version": "qa-version-two", "enabled": True,
            "app": "claude", "title": "QA altro destinatario", "group": "QA other",
            "thread_id": None, "transport": "desktop", "message": "Altro testo",
        }
        message_engine.save_jobs([first, second])
        store = message_engine.TimerStore(self.profile / "logs/messages.sqlite3")
        try:
            for job in (first, second):
                store.prepare(job, now)
        finally:
            store.db.close()
        self.panel.render_jobs()
        return first, second

    def open_job(self, job_id):
        self.panel.job_list.selection_set(job_id)
        self.flush_events()
        self.assertEqual(self.panel.selected_job, job_id)

    def state(self, job_id):
        store = message_engine.TimerStore(self.profile / "logs/messages.sqlite3")
        try:
            return next(row for row in store.rows() if row["id"] == job_id)
        finally:
            store.db.close()

    def test_single_selection_opens_inline_without_scan_or_saving(self):
        first, _ = self.seed()
        before = (self.profile / "messages-config.json").read_bytes()
        self.open_job(first["id"])
        self.assertFalse(self.panel.editor_visible)
        self.assertEqual(self.panel.editor_step, 2)
        self.assertEqual(self.panel.message.get("1.0", "end-1c"), first["message"])
        self.assertEqual(str(self.panel.write_page.pack_info()["in"]), str(self.panel.inline_host))
        self.assertEqual(self.panel.list_view.winfo_manager(), "pack")
        self.assertEqual(self.panel.editor.winfo_manager(), "")
        self.assertEqual((self.profile / "messages-config.json").read_bytes(), before)
        self.native.assert_not_called()

    def test_queued_selection_and_periodic_render_preserve_unsaved_edit(self):
        first, _ = self.seed()
        self.open_job(first["id"])
        self.panel.message.insert("end", "Bozza non salvata")
        self.panel.minutes.set("31")
        self.panel.send_when_active.set(True)
        signature = self.panel.editor_signature()
        self.panel.job_list.selection_set(first["id"])
        self.panel.render_jobs()
        self.panel.last_render = 0
        self.panel.poll()
        self.flush_events()
        self.assertEqual(self.panel.editor_signature(), signature)
        self.assertEqual(message_engine.load_jobs()[0]["message"], first["message"])
        self.confirm.assert_not_called()
        self.native.assert_not_called()

    def test_cancel_other_selection_restores_highlight_and_unsaved_values(self):
        first, second = self.seed()
        self.open_job(first["id"])
        self.panel.message.insert("end", " WIP")
        before = self.panel.editor_signature()
        self.panel.job_list.selection_set(second["id"])
        self.flush_events()
        self.assertTrue(self.unsaved.called)
        self.assertEqual(self.panel.job_list.selection(), (first["id"],))
        self.assertEqual(self.panel.selected_job, first["id"])
        self.assertEqual(self.panel.editor_signature(), before)

    def test_accept_other_selection_loads_only_that_schedule(self):
        first, second = self.seed()
        self.open_job(first["id"])
        self.panel.message.insert("end", " WIP")
        self.unsaved.return_value = "discard"
        self.open_job(second["id"])
        self.assertEqual(self.panel.targets, [second])
        self.assertEqual(self.panel.message.get("1.0", "end-1c"), second["message"])
        self.assertFalse(self.panel.editor_visible)
        self.assertEqual(message_engine.load_jobs()[0]["message"], first["message"])
        self.native.assert_not_called()

    def test_noop_and_options_only_preserve_due_and_uncertain_delivery(self):
        first, _ = self.seed()
        store = message_engine.TimerStore(self.profile / "logs/messages.sqlite3")
        try:
            store.update(first, "uncertain", "QA receipt still pending")
        finally:
            store.db.close()
        self.open_job(first["id"])
        before = self.state(first["id"])
        for options_changed in (False, True):
            with self.subTest(options_changed=options_changed):
                self.panel.send_when_active.set(options_changed)
                self.panel.send_when_present.set(options_changed)
                self.panel.activate()
                self.flush_events()
                saved = next(job for job in message_engine.load_jobs() if job["id"] == first["id"])
                self.assertFalse(saved["enabled"])
                self.assertEqual(saved["version"], first["version"])
                self.assertEqual(saved["activated_at"], first["activated_at"])
                self.assertEqual(saved["send_when_active"], options_changed)
                self.assertEqual(saved["send_when_present"], options_changed)
                self.assertEqual(self.state(first["id"]), before)

    def test_text_edit_stays_paused_changes_version_and_preserves_other_job(self):
        first, second = self.seed()
        other_state = self.state(second["id"])
        self.open_job(first["id"])
        self.panel.message.delete("1.0", "end")
        self.panel.message.insert("1.0", "Nuovo testo letterale\n  con spazi\n")
        self.panel.activate()
        self.flush_events()
        jobs = {job["id"]: job for job in message_engine.load_jobs()}
        self.assertFalse(jobs[first["id"]]["enabled"])
        self.assertNotEqual(jobs[first["id"]]["version"], first["version"])
        self.assertEqual(jobs[first["id"]]["message"], "Nuovo testo letterale\n  con spazi\n")
        self.assertEqual(jobs[second["id"]], second)
        self.assertEqual(self.state(second["id"]), other_state)
        self.assertFalse(self.panel.editor_visible)

    def test_late_catalog_selection_cannot_replace_inline_target_or_text(self):
        first, second = self.seed()
        self.open_job(first["id"])
        self.panel.message.insert("end", " WIP")
        signature = self.panel.editor_signature()
        catalog_row = {key: first[key] for key in ("app", "title", "group", "thread_id")}
        catalog_row["last_activity_at"] = time.time()
        other_row = {key: second[key] for key in ("app", "title", "group", "thread_id")}
        other_row["last_activity_at"] = time.time()
        self.panel.results.put(([catalog_row, other_row], []))
        self.panel.poll()
        self.flush_events()
        self.assertEqual(self.panel.targets, [first])
        self.assertEqual(self.panel.editor_signature(), signature)
        self.native.assert_not_called()

    def test_library_reuse_and_history_keep_inline_target_and_timer(self):
        first, _ = self.seed()
        self.open_job(first["id"])
        before = (self.profile / "messages-config.json").read_bytes()
        before_state = self.state(first["id"])
        self.confirm.return_value = True
        self.assertTrue(self.panel.use_saved_message("È letterale\n  spazio\n"))
        history = Mock()
        self.panel.history_callback = history
        self.panel.show_history()
        history.assert_called_once_with(first["id"])
        self.assertEqual(self.panel.targets, [first])
        self.assertEqual(self.panel.message.get("1.0", "end-1c"), "È letterale\n  spazio\n")
        self.assertEqual((self.profile / "messages-config.json").read_bytes(), before)
        self.assertEqual(self.state(first["id"]), before_state)
        self.assertFalse(self.panel.editor_visible)
        self.native.assert_not_called()

    def test_invalid_save_does_not_mutate_config_and_keeps_work_visible(self):
        first, _ = self.seed()
        self.open_job(first["id"])
        before = (self.profile / "messages-config.json").read_bytes()
        self.panel.minutes.set("not-a-number")
        self.panel.activate()
        self.flush_events()
        self.assertEqual((self.profile / "messages-config.json").read_bytes(), before)
        self.assertEqual(self.panel.minutes.get(), "not-a-number")
        self.assertTrue(self.panel.status.get())
        self.assertEqual(self.panel.write_page.winfo_manager(), "pack")
        self.assertFalse(self.panel.editor_visible)

    def test_new_wizard_is_distinct_and_cancel_preserves_inline_work(self):
        first, _ = self.seed()
        self.open_job(first["id"])
        self.panel.message.insert("end", " WIP")
        signature = self.panel.editor_signature()
        self.assertFalse(self.panel.new())
        self.assertEqual(self.panel.editor_signature(), signature)
        self.assertFalse(self.panel.editor_visible)
        self.unsaved.return_value = "discard"
        with patch.object(self.panel, "refresh") as refresh:
            self.assertTrue(self.panel.new())
        refresh.assert_called_once_with()
        self.assertTrue(self.panel.editor_visible)
        self.assertIsNone(self.panel.selected_job)
        self.assertEqual(self.panel.editor_step, 1)
        self.assertEqual(self.panel.targets, [])
        self.assertFalse(self.panel.send_when_active.get())
        self.assertFalse(self.panel.send_when_present.get())
        self.native.assert_not_called()

    def test_inline_actions_fit_full_workspace_at_both_supported_sizes(self):
        self.cancel_callbacks()
        self.panel.destroy()
        app = SimpleNamespace(root=self.root, rows={}, toggle_automations=lambda: None,
                              restart_reader=lambda: None, refresh_dashboard=lambda *_: None,
                              select=lambda *_: None, show_details=lambda *_: None)
        workspace_ui.build_workspace(app)
        self.panel = app.messages_panel
        first, _ = self.seed()
        self.root.attributes("-alpha", 0.0)
        self.root.attributes("-disabled", True)
        self.root.deiconify()
        app.notebook.select(self.panel)
        self.open_job(first["id"])
        self.cancel_callbacks()
        for dimensions in ("1040x790", "1280x850"):
            with self.subTest(dimensions=dimensions):
                self.root.geometry(dimensions)
                self.flush_events()
                self.assertTrue(self.panel.job_list.winfo_ismapped())
                self.assertTrue(self.panel.message.winfo_ismapped())
                self.assertFalse(self.panel.editor.winfo_ismapped())
                self.assertGreater(self.panel.message.winfo_height(), 55)
                px = self.panel.winfo_rootx()
                py = self.panel.winfo_rooty()
                for button in self.panel.editor_actions.winfo_children():
                    if isinstance(button, ttk.Button):
                        self.assertTrue(button.winfo_ismapped(), button.cget("text"))
                        x = button.winfo_rootx() - px
                        y = button.winfo_rooty() - py
                        self.assertGreaterEqual(x, 0, button.cget("text"))
                        self.assertGreaterEqual(y, 0, button.cget("text"))
                        self.assertLessEqual(x + button.winfo_width(), self.panel.winfo_width(), button.cget("text"))
                        self.assertLessEqual(y + button.winfo_height(), self.panel.winfo_height(), button.cget("text"))
        self.native.assert_not_called()


if __name__ == "__main__":
    unittest.main()
