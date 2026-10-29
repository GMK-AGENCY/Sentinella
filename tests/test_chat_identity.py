"""Private real metadata stores; no writes to either application's live data."""
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from contextlib import closing
from unittest.mock import patch
from types import SimpleNamespace

from chat_identity import resolve,refresh_saved,key
from chat_desktop import timer_target
from chat_desktop import selected_matches,selected_session_id,document_sessions
from claude_delivery import locate,Delivery
from codex_local_sender import thread_metadata
from message_engine import TimerStore


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.home=self.root/'home';self.home.mkdir()
        self.codex_id='11111111-1111-4111-8111-111111111111'
        self.local_id='22222222-2222-4222-8222-222222222222'
        self.cli_id='33333333-3333-4333-8333-333333333333'
        codex=self.home/'.codex';codex.mkdir();self.codex_db=codex/'state_5.sqlite'
        self.history=self.root/'codex-history.jsonl'
        self.history.write_text(json.dumps({'type':'session_meta','payload':{'id':self.codex_id}})+'\n')
        with closing(sqlite3.connect(self.codex_db)) as db, db:
            db.executescript('CREATE TABLE projects(id TEXT,name TEXT); CREATE TABLE threads(id TEXT,name TEXT,title TEXT,cwd TEXT,updated_at_ms INTEGER,updated_at INTEGER,recency_at_ms INTEGER,recency_at INTEGER,project_id TEXT,archived INTEGER,rollout_path TEXT);')
            db.execute("INSERT INTO projects VALUES ('p','Project')")
            db.execute('INSERT INTO threads VALUES (?,?,?,?,?,?,?,?,?,?,?)',(self.codex_id,'Original Codex','Original Codex','C:/Work/Project',1000,1,None,None,'p',0,str(self.history)))
        self.index=self.root/'Local/Claude-Data/claude_test/claude-code-sessions/a/b'/('local_'+self.local_id+'.json')
        self.index.parent.mkdir(parents=True)
        self.meta={'title':'Original Claude','cwd':'C:/Work/Project','isArchived':False,'lastActivityAt':1,'cliSessionId':self.cli_id}
        self.index.write_text(json.dumps(self.meta),encoding='utf-8')
        self.claude_history=self.home/'.claude/projects/project'/(self.cli_id+'.jsonl')
        self.claude_history.parent.mkdir(parents=True);self.claude_history.write_text('')
        for p in (patch('pathlib.Path.home',return_value=self.home),patch.dict(os.environ,{'CODEX_HOME':str(codex),'LOCALAPPDATA':str(self.root/'Local'),'APPDATA':str(self.root/'Roaming')})):
            p.start();self.addCleanup(p.stop)
        common=dict(version='v',enabled=True,activated_at=10,interval_seconds=1800,message='Literal message',repeat=True,group='Project')
        self.codex={**common,'id':'codex','app':'codex','title':'Original Codex','thread_id':self.codex_id,'transport':'codex_app'}
        self.claude={**common,'id':'claude','app':'claude','title':'Original Claude','session_id':self.local_id}

    def rename(self):
        with closing(sqlite3.connect(self.codex_db)) as db, db:db.execute('UPDATE threads SET name=? WHERE id=?',('Renamed Codex',self.codex_id))
        self.meta['title']='Renamed Claude';self.index.write_text(json.dumps(self.meta),encoding='utf-8')

    def test_both_ids_resolve_new_names_without_mutating_jobs_or_other_fields(self):
        self.rename()
        for job,title in ((self.codex,'Renamed Codex'),(self.claude,'Renamed Claude')):
            bound=resolve(job)
            self.assertEqual(bound['title'],title);self.assertEqual(key(bound),key(job))
            for field in ('id','version','activated_at','enabled','message','interval_seconds','repeat'):self.assertEqual(bound[field],job[field])
        self.assertEqual(self.codex['title'],'Original Codex');self.assertEqual(self.claude['title'],'Original Claude')
        self.assertEqual(thread_metadata(self.codex)['name'],'Renamed Codex')
        self.assertEqual(locate(self.claude)['session'],self.cli_id)

    def test_selected_claude_id_ignores_renames_and_embedded_browser_titles(self):
        def document(url):
            return SimpleNamespace(ControlTypeName='DocumentControl',GetValuePattern=lambda:SimpleNamespace(Value=url))
        controls=[document('file:///app/index.html'),document('https://claude.ai/epitaxy/local_'+self.local_id),
                  document('https://example.test/a-page-with-another-title')]
        self.assertTrue(selected_matches(controls,'Unrelated embedded browser title',self.claude))
        self.assertEqual(selected_session_id(controls,'claude'),self.local_id)
        controls.append(document('https://claude.ai/epitaxy/local_'+self.codex_id))
        self.assertIsNone(selected_session_id(controls,'claude'))
        self.assertFalse(selected_matches(controls,'Original Claude',self.claude))
        self.assertEqual(document_sessions([document('https://example.test/epitaxy/local_'+self.local_id)]),[])

    def test_save_from_a_stale_editor_preserves_migrated_ids_and_display_title(self):
        import message_engine
        path=self.root/'messages-config.json';current={**self.claude,'title':'Renamed Claude'}
        path.write_text(json.dumps({'jobs':[current]}))
        stale={k:v for k,v in self.claude.items() if k!='session_id'}
        with patch.object(message_engine,'CONFIG',path):message_engine.save_jobs([stale])
        self.assertEqual(json.loads(path.read_text())['jobs'][0],current)

    def test_native_rows_are_matched_by_id_after_rename_and_group_move(self):
        self.rename()
        for job,title in ((self.codex,'Renamed Codex'),(self.claude,'Renamed Claude')):
            control=object();row={'app':job['app'],'title':title,'group':'Moved sidebar group','ambiguous':False}
            self.assertEqual(timer_target([(row,control)],job)[0][1],control)
            self.assertEqual(timer_target([({**row,'title':job['title']},object())],job),[])

    def test_deleted_or_archived_id_never_falls_back_to_the_old_name(self):
        with closing(sqlite3.connect(self.codex_db)) as db, db:
            db.execute('UPDATE threads SET id=?',('another',))
        self.index.rename(self.index.with_name('local_another.json'))
        for job in (self.codex,self.claude):
            with self.assertRaises(ValueError):resolve(job)
        self.meta['isArchived']=True;self.index.write_text(json.dumps(self.meta))
        with self.assertRaises(ValueError):resolve(self.claude)

    def test_migration_preserves_complete_timer_and_prepared_draft_rows(self):
        logs=self.root/'logs';logs.mkdir()
        store=TimerStore(logs/'messages.sqlite3');self.addCleanup(store.db.close)
        legacy={k:v for k,v in self.claude.items() if k!='session_id'}
        delivery=Delivery(store,self.root);store.prepare(legacy,10);delivery.prepare(legacy)
        before=list(store.db.iterdump());before_job=dict(legacy)
        self.rename()  # Existing owned index proves the renamed legacy recipient.
        path=self.root/'messages-config.json';path.write_text(json.dumps({'jobs':[self.codex,legacy],'other':'preserve'}))
        original=path.read_bytes();report=refresh_saved(self.root)
        saved=json.loads(path.read_text());bound=saved['jobs'][1]
        self.assertEqual(bound['session_id'],self.local_id);self.assertEqual(bound['title'],'Renamed Claude')
        for field,value in before_job.items():
            if field!='title':self.assertEqual(bound[field],value)
        self.assertEqual(list(store.db.iterdump()),before)
        self.assertEqual(saved['other'],'preserve')
        self.assertIn(original,[p.read_bytes() for p in (self.root/'backups').iterdir()])
        self.assertEqual(report['unresolved'],[])
        self.assertEqual(refresh_saved(self.root)['updated'],[])

    def test_legacy_duplicate_names_remain_unbound_without_a_receipt(self):
        self.index.with_name('local_other.json').write_bytes(self.index.read_bytes())
        legacy={k:v for k,v in self.claude.items() if k!='session_id'}
        path=self.root/'messages-config.json';path.write_text(json.dumps({'jobs':[legacy]}));before=path.read_bytes()
        report=refresh_saved(self.root)
        self.assertEqual(path.read_bytes(),before);self.assertEqual(len(report['unresolved']),1)

    def test_claude_prepared_ownership_and_receipt_survive_rename(self):
        store=TimerStore(self.root/'state.sqlite3');self.addCleanup(store.db.close)
        state=store.prepare(self.claude,10);delivery=Delivery(store,self.root);delivery.prepare(self.claude)
        row=delivery.current(self.claude);self.rename();delivery.prepare(self.claude)
        self.assertEqual(delivery.current(self.claude)['token'],row['token'])
        record={'type':'user','sessionId':self.cli_id,'uuid':'receipt','timestamp':'2099-01-01T00:00:00Z','message':{'role':'user','content':self.claude['message']}}
        self.claude_history.write_text(json.dumps(record)+'\n')
        self.assertEqual(delivery.reconcile(self.claude,state)['action'],'receipt_confirmed')
        self.assertEqual(store.db.execute('SELECT count(*) FROM claude_timer_deliveries').fetchone()[0],1)
