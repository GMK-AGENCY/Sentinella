"""Destructive commands are exercised only on disposable application profiles."""
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
import messages_panel
from notification_core import Notification, after_cleanup
import settings_panel
import workspace_ui


class CleanupControlTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.profile=Path(self.temp.name)
        for patcher in (
            patch.dict(os.environ,{'SENTINELLA_DATA_DIR':str(self.profile)}),
            patch.object(automation_policy,'ROOT',self.profile),
            patch.object(message_engine,'ROOT',self.profile),
            patch.object(message_engine,'CONFIG',self.profile/'messages-config.json'),
            patch.object(messages_panel,'ROOT',self.profile),
            patch.object(settings_panel,'ROOT',self.profile),
            patch.object(approvals_panel,'ROOT',self.profile),
            patch.object(workspace_ui,'ROOT',self.profile),
            patch('bounded_process.run_bounded',side_effect=AssertionError('Native calls forbidden')),
            patch('workspace_ui.startup_enabled',return_value=False),
        ):
            patcher.start();self.addCleanup(patcher.stop)
        ensure_local_files(self.profile)
        self.root=tk.Tk();self.root.withdraw()
        self.root.attributes('-alpha',0.0);self.root.attributes('-disabled',True)
        self.addCleanup(self.close)
        self.app=SimpleNamespace(root=self.root,rows={},toggle_automations=lambda:None,
            restart_reader=lambda:None,refresh_dashboard=lambda *_:None,
            select=lambda *_:None,show_details=lambda *_:None)
        workspace_ui.build_workspace(self.app)

    def close(self):
        for item in self.root.tk.call('after','info'):self.root.tk.call('after','cancel',item)
        self.root.destroy()

    @staticmethod
    def synchronous(_parent,_button,operation,on_done):
        on_done(operation())

    def test_clear_all_ignores_filters_and_preserves_jobs_timers_and_external_files(self):
        jobs=[dict(id=f'job-{i}',version='v',app='claude',title=f'Chat {i}',group='Test',
                   interval_seconds=900,message='Private example',enabled=True,activated_at=time.time()) for i in range(2)]
        message_engine.save_jobs(jobs)
        store=message_engine.TimerStore(self.profile/'logs/messages.sqlite3')
        for job in jobs:store.prepare(job,time.time())
        timers=store.rows();store.db.close()
        before=(self.profile/'messages-config.json').read_bytes()
        unrelated=self.profile/'logs/unrelated.txt';unrelated.write_text('Keep me')
        panel=self.app.history_panel
        panel.refresh();panel.target.set('Chat 0');panel.outcome.set('Attese e preparazione')
        with patch('workspace_ui.messagebox.askyesno',return_value=False):
            self.assertFalse(panel.clear_all())
        store=message_engine.TimerStore(self.profile/'logs/messages.sqlite3')
        self.assertEqual(len(store.history()),2);store.db.close()
        with patch('workspace_ui.messagebox.askyesno',return_value=True) as confirmation, \
             patch('workspace_ui.start_cleanup',side_effect=self.synchronous):
            self.assertTrue(panel.clear_all())
        self.assertIn('anche quelli esclusi dai filtri',confirmation.call_args.args[1])
        store=message_engine.TimerStore(self.profile/'logs/messages.sqlite3')
        self.assertEqual(store.history(),[]);self.assertEqual(store.rows(),timers);store.db.close()
        self.assertEqual((self.profile/'messages-config.json').read_bytes(),before)
        self.assertEqual(unrelated.read_text(),'Keep me')

    def test_notifications_purge_preserves_new_arrivals_dedup_and_other_journal_events(self):
        values=dict(app='Claude',app_id='claude',created='2026-10-28T00:00:00+00:00',title='Chat',body='Private payload')
        old=Notification(**values,notification_id=1)
        self.app.rows['old']=old
        self.app.tree.insert('','end',iid='old',values=('time','Claude','Notifica','Chat'))
        path=self.profile/'logs/events.jsonl'
        path.write_text(json.dumps({'type':'notification',**values})+'\n'+json.dumps({'type':'status','state':'ready'})+'\n',encoding='utf-8')
        initial_size=path.stat().st_size
        new=Notification(**{**values,'created':'2099-01-01T00:00:00Z'},notification_id=2)
        def arrive(_parent,_button,operation,on_done):
            result=operation()
            self.app.rows['new']=new
            self.app.tree.insert('','end',iid='new',values=('later','Claude','Notifica','New'))
            on_done(result)
        with patch('workspace_ui.messagebox.askyesno',return_value=False):
            self.assertFalse(self.app.clear_notifications())
        self.assertIn('old',self.app.rows)
        with patch('workspace_ui.messagebox.askyesno',return_value=True),patch('workspace_ui.start_cleanup',side_effect=arrive):
            self.assertTrue(self.app.clear_notifications())
        self.assertEqual(set(self.app.rows),{'new'})
        self.assertEqual(self.app.tree.get_children(),('new',))
        self.assertEqual(path.stat().st_size,initial_size)
        self.assertNotIn('Private payload',path.read_text())
        self.assertIn('ready',path.read_text())
        self.assertFalse(after_cleanup(old,self.app.notifications_cleared_at))
        self.assertTrue(after_cleanup(new,self.app.notifications_cleared_at))

    def test_blue_cleanup_buttons_remain_right_of_filters_at_supported_sizes(self):
        self.root.deiconify()
        for dimensions in ('1040x790','1280x850'):
            self.root.geometry(dimensions)
            for panel,button in ((self.app.history_panel,self.app.history_panel.delete_all_button),
                                 (self.app.navigation.pages[self.app.notebook.tabs()[4]],self.app.notification_delete_button)):
                self.app.navigate_to(panel);self.root.update();self.root.update_idletasks()
                self.assertTrue(button.winfo_ismapped())
                self.assertEqual(button.cget('style'),'Primary.TButton')
                self.assertTrue(button.cget('image'))
                parent=button.master
                siblings=[w for w in parent.winfo_children() if isinstance(w,(ttk.Entry,ttk.Combobox))]
                self.assertGreaterEqual(button.winfo_x(),max(w.winfo_x()+w.winfo_width() for w in siblings))
                self.assertLessEqual(button.winfo_x()+button.winfo_width(),parent.winfo_width())


if __name__=='__main__':unittest.main()
