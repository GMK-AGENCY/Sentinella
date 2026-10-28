"""Diagnostic feedback requires observed completion, never just a click."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch

import approvals_panel
import automation_policy
from config_store import ensure_local_files
import settings_panel
import workspace_ui


class SettingsDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.profile=Path(self.temporary.name)
        ensure_local_files(self.profile)
        (self.profile/'logs').mkdir(exist_ok=True)
        self.patches=[patch.object(module,'ROOT',self.profile) for module in
                      (settings_panel,workspace_ui,approvals_panel,automation_policy)]
        for p in self.patches:p.start()
        self.root=tk.Tk();self.root.withdraw()
        self.panel=settings_panel.SettingsPanel(self.root,lambda *_:None,lambda:None,lambda:None)

    def tearDown(self):
        for callback in self.root.tk.splitlist(self.root.tk.call('after','info')):
            self.root.after_cancel(callback)
        self.root.destroy()
        for p in reversed(self.patches):p.stop()
        self.temporary.cleanup()

    def spin(self,seconds=0.35):
        until=time.monotonic()+seconds
        while time.monotonic()<until:
            self.root.update();time.sleep(0.01)

    def health(self,scan,fresh=True,state='active'):
        now=datetime.now(timezone.utc)
        (self.profile/'logs/health.json').write_text(json.dumps({
            'state':state,'updated_at':now.isoformat() if fresh else (now-timedelta(minutes=2)).isoformat(),
            'last_successful_scan':scan.isoformat() if scan else None}),encoding='utf-8')

    def test_restart_waits_for_new_scan_and_restores_controls(self):
        old=datetime.now(timezone.utc)-timedelta(minutes=1)
        self.health(old)
        self.panel.restart_notifications();self.spin()
        self.assertTrue(self.panel.diagnostic_busy)
        self.assertTrue(all(b.instate(['disabled']) for b in self.panel.diagnostic_buttons))
        self.health(datetime.now(timezone.utc));self.spin()
        self.assertFalse(self.panel.diagnostic_busy)
        self.assertIn('nuova lettura',self.panel.diagnostic_feedback.get())
        self.assertTrue(all(not b.instate(['disabled']) for b in self.panel.diagnostic_buttons))

    def test_blocked_reader_and_exception_are_not_reported_as_success(self):
        self.panel.reader_restart=lambda:self.health(None,state='blocked')
        self.panel.restart_notifications();self.spin()
        self.assertIn('non disponibile',self.panel.diagnostic_feedback.get())
        self.assertFalse(self.panel.diagnostic_busy)
        def fail():raise OSError('QA failure')
        self.panel.reader_restart=fail
        self.panel.restart_notifications();self.spin()
        self.assertIn('QA failure',self.panel.diagnostic_feedback.get())
        self.assertFalse(self.panel.diagnostic_busy)

    def test_detection_reports_names_without_changing_pause_or_jobs(self):
        before=(self.profile/'messages-config.json').read_bytes()
        paused=automation_policy.load_config()['paused']
        with patch('settings_panel.detect_running_apps',return_value={'codex':'C:/Example/Codex.exe'}):
            self.panel.detect_apps()
            self.assertIn('corso',self.panel.diagnostic_feedback.get())
            self.spin()
        self.assertIn('Codex (1)',self.panel.diagnostic_feedback.get())
        self.assertEqual(automation_policy.load_config()['paused'],paused)
        self.assertEqual((self.profile/'messages-config.json').read_bytes(),before)

    def test_detection_failure_is_visible_and_retry_available(self):
        with patch('settings_panel.detect_running_apps',side_effect=OSError('QA failure')):
            self.panel.detect_apps();self.spin()
        self.assertIn('QA failure',self.panel.diagnostic_feedback.get())
        self.assertFalse(self.panel.diagnostic_busy)


if __name__=='__main__':unittest.main()
