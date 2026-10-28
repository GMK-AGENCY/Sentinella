"""Real stock-Tk workspace navigation, using private state and no native apps.

Run under an external 45-second process deadline. Modal choices, native catalog
workers and Windows startup state are mocked; program widgets/handlers are real.
"""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
from types import SimpleNamespace
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import Mock, patch

import approvals_panel
import automation_policy
from config_store import ensure_local_files
import message_engine
import messages_panel
import notification_watch
import settings_panel
import startup_manager
from switch_control import Switch
import workspace_ui


SOURCE = Path(__file__).resolve().parents[1]


class NavigationFlowTests(unittest.TestCase):
    def setUp(self):
        self.production = {name: hashlib.sha256((SOURCE/name).read_bytes()).hexdigest()
                           for name in ('approval-config.json','messages-config.json') if (SOURCE/name).exists()}
        self.temporary = tempfile.TemporaryDirectory(prefix='SentinellaNavigationQA_')
        self.profile = Path(self.temporary.name)
        ensure_local_files(self.profile)
        self.private_controls=(self.profile/'approval-config.json').read_bytes()
        (self.profile/'logs').mkdir()
        now=time.time()
        self.jobs=[dict(id=f'qa-{i}',version=f'version-{i}',app='claude',title=f'QA chat {i}',
                        group='Synthetic project',message=f'Saved literal message {i}',enabled=i%3!=1,
                        activated_at=now-10,interval_seconds=900,repeat=True,
                        send_when_active=False,send_when_present=False) for i in range(10)]
        (self.profile/'messages-config.json').write_text(json.dumps({'jobs':self.jobs}),encoding='utf-8')
        store=message_engine.TimerStore(self.profile/'logs/messages.sqlite3')
        try:
            for job in self.jobs:store.prepare(job,now)
            store.update(self.jobs[1],'uncertain','Uncertain synthetic reservation')
            store.event(self.jobs[0],'Consegnato con ricevuta','Synthetic persisted receipt',at=now-15)
            store.db.execute('CREATE TABLE connector_deliveries (token TEXT PRIMARY KEY,job_id TEXT,version TEXT,thread_id TEXT,message_hash TEXT,reserved_at REAL,confirmed_at REAL,receipt TEXT)')
            store.db.execute('INSERT INTO connector_deliveries VALUES (?,?,?,?,?,?,NULL,NULL)',
                             ('synthetic-reservation','qa-1','version-1','11111111-1111-4111-8111-111111111111','sha256',now))
            store.db.commit()
        finally:store.db.close()
        self.patches=[patch.dict(os.environ,{'SENTINELLA_DATA_DIR':str(self.profile)}),
                      patch.object(automation_policy,'ROOT',self.profile),
                      patch.object(message_engine,'ROOT',self.profile),
                      patch.object(message_engine,'CONFIG',self.profile/'messages-config.json'),
                      patch.object(messages_panel,'ROOT',self.profile),
                      patch.object(workspace_ui,'ROOT',self.profile),
                      patch.object(settings_panel,'ROOT',self.profile),
                      patch.object(approvals_panel,'ROOT',self.profile),
                      patch.object(startup_manager,'ROOT',self.profile),
                      patch.object(notification_watch,'ROOT',self.profile),
                      patch.object(notification_watch,'LOGS',self.profile/'logs'),
                      patch('workspace_ui.startup_enabled',return_value=False),
                      patch('settings_panel.set_startup',side_effect=AssertionError('No startup changes')),
                      patch('bounded_process.run_bounded',return_value={'stdout':'{"chats":[]}', 'stderr':'',
                                                                       'returncode':0,'timed_out':False}),
                      patch('unsaved_changes._choose_unsaved',return_value=None),
                      patch('messages_panel.messagebox.askyesno',return_value=False)]
        self.mocks=[patcher.start() for patcher in self.patches]
        self.choice=self.mocks[-2];self.confirm=self.mocks[-1]
        self.root=tk.Tk();self.root.withdraw();self.root.attributes('-alpha',0);self.root.attributes('-disabled',True)
        self.callback_errors=[]
        self.root.report_callback_exception=lambda *args:self.callback_errors.append(args)
        self.app=SimpleNamespace(root=self.root,rows={},toggle_automations=Mock(),restart_reader=Mock(),
                                 refresh_dashboard=Mock(),select=Mock(),show_details=Mock(),
                                 finish_gui_profile=Mock(),stop_worker=Mock(),write_health=Mock(),log=Mock(),
                                 diagnostics=SimpleNamespace(close=Mock()),supervisor=None,monitoring=True,run_id='synthetic-qa')
        workspace_ui.build_workspace(self.app)
        self.panel=self.app.messages_panel
        self.root.deiconify();self.pump()

    def tearDown(self):
        try:
            for callback in self.root.tk.call('after','info'):
                self.root.tk.call('after','cancel',callback)
            self.root.destroy()
        finally:
            for patcher in reversed(self.patches):patcher.stop()
            self.temporary.cleanup()
        self.assertFalse(self.callback_errors)
        for name,digest in self.production.items():
            self.assertEqual(hashlib.sha256((SOURCE/name).read_bytes()).hexdigest(),digest)

    def pump(self):
        self.root.update();self.root.update_idletasks()

    def row_action(self,job_id,action):
        # Hosted Windows runners may have a narrower desktop. Exercise a real
        # visible control, never invoke a button clipped outside the viewport.
        self.panel.job_list.see(job_id)
        self.panel.job_list.xview_moveto(1)
        self.panel.row_actions.refresh();self.pump()
        button=self.panel.row_actions.buttons[job_id][action]
        self.assertTrue(button.winfo_ismapped(),(job_id,action,self.panel.job_list.xview()))
        return button

    def saved(self, job_id):
        return next((job for job in message_engine.load_jobs() if job['id']==job_id),None)

    def edit(self, job_id='qa-0'):
        self.assertTrue(self.app.navigate_to(self.panel))
        self.assertTrue(self.panel.edit_job_id(job_id))
        self.pump()
        self.assertEqual(self.panel.selected_job,job_id)

    def change_text(self, text='Uncommitted native-widget text'):
        self.panel.message.delete('1.0','end');self.panel.message.insert('1.0',text)
        self.assertTrue(self.panel.is_dirty())

    def sidebar(self, label):
        def walk(widget):
            for child in widget.winfo_children():
                yield child
                yield from walk(child)
        return next(widget for widget in walk(self.root)
                    if isinstance(widget,tk.Button) and widget.cget('text')==label)

    def ledger(self):
        db=sqlite3.connect((self.profile/'logs/messages.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=.2)
        try:
            db.execute('PRAGMA query_only=ON')
            return {table:list(db.execute('SELECT * FROM '+table+' ORDER BY rowid'))
                    for table in ('timers','connector_deliveries','message_events')}
        finally:db.close()

    def test_sidebar_save_commits_exact_selected_job_and_navigates(self):
        self.edit('qa-1');self.change_text('Saved via sidebar')
        self.choice.return_value='save'
        self.sidebar('Messaggi salvati').invoke();self.pump()
        self.assertEqual(self.app.notebook.select(),str(self.app.library_panel))
        self.assertEqual(self.saved('qa-1')['message'],'Saved via sidebar')
        self.assertFalse(self.saved('qa-1')['enabled'])
        self.assertEqual(self.saved('qa-0')['message'],self.jobs[0]['message'])
        self.assertFalse(self.panel.is_dirty())

    def test_sidebar_discard_restores_saved_editor_and_navigates(self):
        self.edit();self.change_text()
        before=(self.profile/'messages-config.json').read_bytes()
        self.choice.return_value='discard'
        self.sidebar('Messaggi salvati').invoke();self.pump()
        self.assertEqual(self.app.notebook.select(),str(self.app.library_panel))
        self.assertEqual(self.panel.message.get('1.0','end-1c'),self.jobs[0]['message'])
        self.assertEqual((self.profile/'messages-config.json').read_bytes(),before)
        self.assertFalse(self.panel.is_dirty())

    def test_sidebar_cancel_keeps_dirty_editor_and_selection(self):
        self.edit();self.change_text()
        self.sidebar('Messaggi salvati').invoke();self.pump()
        self.assertEqual(self.app.notebook.select(),str(self.panel))
        self.assertEqual(self.app.navigation.current,str(self.panel))
        self.assertEqual(self.panel.selected_job,'qa-0')
        self.assertEqual(self.panel.message.get('1.0','end-1c'),'Uncommitted native-widget text')
        self.assertTrue(self.panel.is_dirty())

    def test_invalid_save_does_not_leave_page_or_partially_save(self):
        self.edit();self.change_text('')
        before=(self.profile/'messages-config.json').read_bytes()
        self.choice.return_value='save'
        self.assertFalse(self.app.navigate_to(self.app.library_panel));self.pump()
        self.assertEqual(self.app.notebook.select(),str(self.panel))
        self.assertEqual((self.profile/'messages-config.json').read_bytes(),before)
        self.assertTrue(self.panel.status.get())
        self.assertTrue(self.panel.is_dirty())

    def test_direct_native_notebook_select_is_guarded_and_restores_on_cancel(self):
        self.edit();self.change_text()
        self.app.notebook.select(self.app.library_panel);self.pump()
        self.assertEqual(self.app.notebook.select(),str(self.panel))
        self.assertEqual(self.app.navigation.current,str(self.panel))
        self.assertEqual(self.choice.call_count,1)
        self.assertTrue(self.panel.is_dirty())

    def test_direct_native_notebook_save_and_discard_resolve_before_switch(self):
        for choice in ('save','discard'):
            with self.subTest(choice=choice):
                self.edit()
                persisted=self.saved('qa-0')['message']
                self.change_text('Native notebook '+choice)
                self.choice.return_value=choice
                self.app.notebook.select(self.app.library_panel);self.pump()
                self.assertEqual(self.app.notebook.select(),str(self.app.library_panel))
                self.assertEqual(self.app.navigation.current,str(self.app.library_panel))
                self.assertEqual(self.saved('qa-0')['message'],
                                 'Native notebook save' if choice=='save' else persisted)
                self.assertFalse(self.panel.is_dirty())

    def test_sqlite_failure_after_file_commit_keeps_success_and_clean_baseline(self):
        self.edit('qa-1');self.change_text('Committed despite unavailable journal')
        with patch('messages_panel.TimerStore',side_effect=sqlite3.OperationalError('Synthetic database busy')):
            self.assertTrue(self.panel.activate())
        self.assertEqual(self.saved('qa-1')['message'],'Committed despite unavailable journal')
        self.assertFalse(self.saved('qa-1')['enabled'])
        self.assertFalse(self.panel.is_dirty())
        self.assertIn('salvata',self.panel.status.get().casefold())
        self.assertIn('riproverà il timer',self.panel.status.get())
        self.assertIn('Synthetic database busy',self.panel.status.get())

    def test_file_save_failure_keeps_exact_config_dirty_editor_and_no_database_write(self):
        self.edit('qa-1');self.change_text('Still uncommitted')
        before=(self.profile/'messages-config.json').read_bytes();ledger=self.ledger()
        with patch('messages_panel.save_jobs',side_effect=OSError('Synthetic file failure')), \
             patch('messages_panel.TimerStore') as journal:
            self.assertFalse(self.panel.activate())
            journal.assert_not_called()
        self.assertEqual((self.profile/'messages-config.json').read_bytes(),before)
        self.assertEqual(self.ledger(),ledger)
        self.assertEqual(self.panel.message.get('1.0','end-1c'),'Still uncommitted')
        self.assertEqual(self.panel.selected_job,'qa-1')
        self.assertTrue(self.panel.is_dirty())
        self.assertIn('Synthetic file failure',self.panel.status.get())

    def test_abandoned_new_wizard_creates_no_schedule(self):
        self.assertTrue(self.app.navigate_to(self.panel));self.panel.new();self.pump()
        before=(self.profile/'messages-config.json').read_bytes()
        self.panel.targets=[{'app':'claude','title':'New synthetic target','group':'Synthetic project'}]
        self.panel.update_target_summary();self.panel.next_step();self.change_text('Not saved')
        self.choice.return_value='discard'
        self.assertTrue(self.app.navigate_to(self.app.library_panel));self.pump()
        self.assertEqual((self.profile/'messages-config.json').read_bytes(),before)
        self.assertIsNone(self.panel.selected_job)
        self.assertFalse(self.panel.is_dirty())

    def test_close_cancel_reveals_hidden_dirty_page_without_finishing(self):
        self.panel.load_editor(self.jobs[0]);self.change_text('Hidden dirty editor')
        self.assertNotEqual(self.app.notebook.select(),str(self.panel))
        notification_watch.Monitor.close(self.app);self.pump()
        self.app.finish_gui_profile.assert_not_called();self.app.stop_worker.assert_not_called()
        self.assertTrue(self.app.monitoring)
        self.assertTrue(self.root.winfo_exists())
        self.assertEqual(self.app.notebook.select(),str(self.panel))
        self.assertTrue(self.panel.is_dirty())
        self.assertFalse((self.profile/'logs/exit-intent.json').exists())

    def test_library_save_and_discard_use_the_same_navigation_guard(self):
        library=self.app.library_panel
        self.assertTrue(self.app.navigate_to(library));library._load()
        library.title_value.set('Navigation QA template');library.text.insert('1.0','Saved library literal')
        self.choice.return_value='save'
        self.assertTrue(self.app.navigate_to(self.panel));self.pump()
        item=next(row for row in library.store.list() if row['title']=='Navigation QA template')
        self.assertEqual(item['text'],'Saved library literal')
        self.assertTrue(self.app.navigate_to(library));library._load(item)
        library.text.insert('end',' unsaved addition')
        self.choice.return_value='discard'
        self.assertTrue(self.app.navigate_to(self.panel));self.pump()
        self.assertEqual(library.text.get('1.0','end-1c'),'Saved library literal')
        self.assertFalse(library.is_dirty())

    def test_library_invalid_save_blocks_navigation_and_preserves_editor(self):
        library=self.app.library_panel
        self.assertTrue(self.app.navigate_to(library));library._load()
        library.title_value.set(' ');library.text.insert('1.0','Unsaved library text')
        before={path.name:path.read_bytes() for path in self.profile.glob('message-library*')}
        self.choice.return_value='save'
        self.assertFalse(self.app.navigate_to(self.panel));self.pump()
        self.assertEqual(self.app.notebook.select(),str(library))
        self.assertEqual(library.text.get('1.0','end-1c'),'Unsaved library text')
        self.assertTrue(library.title_error.get());self.assertTrue(library.is_dirty())
        self.assertEqual({path.name:path.read_bytes() for path in self.profile.glob('message-library*')},before)

    def test_close_save_persists_before_finishing_and_invalid_save_cannot_close(self):
        self.edit();self.change_text('')
        self.choice.return_value='save'
        notification_watch.Monitor.close(self.app)
        self.app.finish_gui_profile.assert_not_called()
        self.assertFalse((self.profile/'logs/exit-intent.json').exists())
        self.change_text('Saved before closing')
        with patch.object(self.root,'destroy') as destroy:
            notification_watch.Monitor.close(self.app)
            destroy.assert_called_once_with()
        self.assertEqual(self.saved('qa-0')['message'],'Saved before closing')
        self.app.finish_gui_profile.assert_called_once_with()
        self.app.stop_worker.assert_called_once_with()
        self.assertFalse(self.app.monitoring)
        self.assertTrue((self.profile/'logs/exit-intent.json').is_file())

    def test_table_selection_cancel_then_discard_never_loads_wrong_source(self):
        self.edit();self.change_text('Keep selected source')
        self.panel.job_list.selection_set('qa-2');self.pump()
        self.assertEqual(self.panel.selected_job,'qa-0')
        self.assertEqual(self.panel.job_list.selection(),('qa-0',))
        self.assertEqual(self.panel.message.get('1.0','end-1c'),'Keep selected source')
        self.choice.return_value='discard'
        self.panel.job_list.selection_set('qa-2');self.pump()
        self.assertEqual(self.panel.selected_job,'qa-2')
        self.assertEqual(self.panel.message.get('1.0','end-1c'),self.jobs[2]['message'])
        self.assertEqual(self.saved('qa-0')['message'],self.jobs[0]['message'])

    def test_chosen_table_id_is_the_authoritative_editor_source(self):
        self.assertTrue(self.app.navigate_to(self.panel))
        self.panel.job_list.selection_set('qa-4');self.pump()
        self.assertEqual(self.panel.selected_job,'qa-4')
        self.assertEqual(self.panel.message.get('1.0','end-1c'),self.jobs[4]['message'])
        self.assertEqual(self.panel.minutes.get(),'15')
        self.assertFalse(self.saved('qa-4')['enabled'])

    def test_row_toggle_does_not_select_or_overwrite_another_dirty_editor(self):
        self.edit();self.change_text()
        before=self.saved('qa-1');self.panel.row_actions.refresh();self.pump()
        button=self.row_action('qa-1','toggle')
        self.assertEqual(button.cget('text'),'Riprendi')
        button.invoke();self.pump()
        self.assertTrue(self.saved('qa-1')['enabled'])
        self.assertEqual(button.cget('text'),'Pausa')
        self.assertEqual(self.saved('qa-1')['version'],before['version'])
        self.assertEqual(self.panel.selected_job,'qa-0')
        self.assertEqual(self.panel.message.get('1.0','end-1c'),'Uncommitted native-widget text')
        self.assertTrue(self.panel.is_dirty())

    def test_delete_cancel_then_confirm_retains_ledger_history_and_other_wip(self):
        self.edit();self.change_text()
        before=self.ledger();self.panel.row_actions.refresh();self.pump()
        button=self.row_action('qa-1','delete')
        self.assertEqual(str(button.cget('compound')),'none')
        self.confirm.return_value=False;button.invoke();self.pump()
        self.assertIsNotNone(self.saved('qa-1'))
        self.confirm.return_value=True;button.invoke();self.pump()
        self.assertIsNone(self.saved('qa-1'))
        after=self.ledger()
        self.assertEqual(after,before)
        self.assertEqual(self.panel.selected_job,'qa-0')
        self.assertEqual(self.panel.message.get('1.0','end-1c'),'Uncommitted native-widget text')
        self.assertTrue(self.panel.is_dirty())

    def test_program_controls_options_and_actions_fit_supported_sizes(self):
        self.edit()
        def walk(widget):
            for child in widget.winfo_children():
                yield child
                yield from walk(child)
        for dimensions in ('1040x790','1280x850'):
            self.root.geometry(dimensions);self.panel.row_actions.refresh();self.pump()
            for button in walk(self.panel):
                if isinstance(button,ttk.Button) and button.winfo_ismapped():
                    x=button.winfo_rootx()-self.root.winfo_rootx();y=button.winfo_rooty()-self.root.winfo_rooty()
                    self.assertGreaterEqual(x,0,(dimensions,button.cget('text')))
                    self.assertGreaterEqual(y,0,(dimensions,button.cget('text')))
                    self.assertLessEqual(x+button.winfo_width(),self.root.winfo_width(),(dimensions,button.cget('text')))
                    self.assertLessEqual(y+button.winfo_height(),self.root.winfo_height(),(dimensions,button.cget('text')))
            children=self.panel.option_columns.winfo_children()
            for row in (0,1):
                row_children=[child for child in children if int(child.grid_info()['row'])==row]
                self.assertEqual(sorted(int(child.grid_info()['column']) for child in row_children),[0,1,2,3])
            for child in children:
                self.assertLessEqual(child.winfo_x()+child.winfo_width(),self.panel.option_columns.winfo_width())
            self.assertEqual(sum(isinstance(child,Switch) for child in children),3)
            for iid,buttons in self.panel.row_actions.buttons.items():
                if buttons['delete'].winfo_ismapped():
                    last=self.panel.job_list.bbox(iid,'last')
                    self.assertGreaterEqual(buttons['toggle'].winfo_x(),last[0]+last[2])
                    self.assertGreater(buttons['delete'].winfo_x(),buttons['toggle'].winfo_x())

    def test_wizard_and_library_buttons_fit_supported_sizes_without_saving(self):
        before=(self.profile/'messages-config.json').read_bytes()
        def walk(widget):
            for child in widget.winfo_children():
                yield child
                yield from walk(child)
        for dimensions in ('1040x790','1280x850'):
            self.root.geometry(dimensions)
            self.assertTrue(self.app.navigate_to(self.panel));self.assertTrue(self.panel.new());self.pump()
            for step in (1,2):
                if step==2:
                    self.panel.targets=[{'app':'claude','title':'New synthetic target','group':'Synthetic project'}]
                    self.panel.update_target_summary();self.panel.next_step()
                self.pump()
                for button in walk(self.panel):
                    if isinstance(button,ttk.Button) and button.winfo_ismapped():
                        x=button.winfo_rootx()-self.root.winfo_rootx();y=button.winfo_rooty()-self.root.winfo_rooty()
                        self.assertGreaterEqual(x,0,(dimensions,step,button.cget('text')))
                        self.assertGreaterEqual(y,0,(dimensions,step,button.cget('text')))
                        self.assertLessEqual(x+button.winfo_width(),self.root.winfo_width(),(dimensions,step,button.cget('text')))
                        self.assertLessEqual(y+button.winfo_height(),self.root.winfo_height(),(dimensions,step,button.cget('text')))
            self.choice.return_value='discard'
            self.assertTrue(self.app.navigate_to(self.app.library_panel));self.pump()
            for button in walk(self.app.library_panel):
                if isinstance(button,ttk.Button) and button.winfo_ismapped():
                    x=button.winfo_rootx()-self.root.winfo_rootx();y=button.winfo_rooty()-self.root.winfo_rooty()
                    self.assertGreaterEqual(x,0,(dimensions,button.cget('text')))
                    self.assertGreaterEqual(y,0,(dimensions,button.cget('text')))
                    self.assertLessEqual(x+button.winfo_width(),self.root.winfo_width(),(dimensions,button.cget('text')))
                    self.assertLessEqual(y+button.winfo_height(),self.root.winfo_height(),(dimensions,button.cget('text')))
        self.assertEqual((self.profile/'messages-config.json').read_bytes(),before)
        self.assertEqual((self.profile/'approval-config.json').read_bytes(),self.private_controls)


if __name__=='__main__':unittest.main()
