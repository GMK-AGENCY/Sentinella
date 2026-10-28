"""Cleanup uses only isolated profiles; operational state must stay byte intact."""
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

import history_cleanup as cleanup
from message_engine import TimerStore


class HistoryCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.root=Path(self.temporary.name)/'LocalAppData'/'Sentinella'
        self.logs=self.root/'logs';self.logs.mkdir(parents=True)
        self.confirmed=str(uuid.uuid4());self.pending=str(uuid.uuid4())
        self.job={'id':'job','version':'v','title':'Private QA','group':'QA','app':'codex','interval_seconds':60,'message':'Private pending draft'}
        self.store=TimerStore(self.logs/'messages.sqlite3');self.store.prepare(self.job,100)
        self.store.update(self.job,'uncertain','Non ripetere: ricevuta in attesa')
        db=self.store.db
        db.execute('CREATE TABLE connector_deliveries(token TEXT PRIMARY KEY,job_id TEXT,version TEXT,thread_id TEXT,message_hash TEXT,reserved_at REAL,confirmed_at REAL,receipt TEXT)')
        db.executemany('INSERT INTO connector_deliveries VALUES(?,?,?,?,?,?,?,?)',[(self.confirmed,'job','v','thread','hash',90,5000,'actual receipt'),(self.pending,'job','v','thread','hash',120,None,None)])
        db.execute('INSERT INTO message_retry VALUES(?,?,?)',('job','v',600))
        db.execute('CREATE TABLE local_codex_deliveries(token TEXT,stage TEXT,scan_offset INTEGER)')
        db.execute('INSERT INTO local_codex_deliveries VALUES(?,?,?)',(self.pending,'awaiting_receipt',123))
        db.execute('CREATE TABLE claude_timer_deliveries(token TEXT,stage TEXT,message TEXT)')
        db.execute('INSERT INTO claude_timer_deliveries VALUES(?,?,?)',('claude-token','prepared','Owned draft'))
        db.executemany('INSERT INTO message_events(at,job_id,title,event,detail) VALUES(?,?,?,?,?)',[(i,'job','Private QA','In attesa','Full matching history') for i in range(350)])
        db.commit()
        self.protected={name:list(db.execute('SELECT * FROM '+name)) for name in ('timers','message_retry','connector_deliveries','local_codex_deliveries','claude_timer_deliveries')}
        self.files={}
        for name in ('approval-config.json','messages-config.json','message-library.json'):
            path=self.root/name;path.write_bytes(b'User-owned configuration');self.files[path]=path.read_bytes()
        drafts=self.logs/'preserved-drafts';drafts.mkdir();path=drafts/'owned.json';path.write_bytes(b'Owned draft');self.files[path]=path.read_bytes()

    def tearDown(self):
        self.store.db.close();self.temporary.cleanup()

    def assert_protected(self):
        for name,rows in self.protected.items():self.assertEqual(list(self.store.db.execute('SELECT * FROM '+name)),rows,name)
        for path,payload in self.files.items():self.assertEqual(path.read_bytes(),payload,str(path))

    def test_delivery_clears_all_events_not_only_two_hundred_and_does_not_reimport_receipts(self):
        with patch('message_engine.load_jobs',return_value=[self.job]):
            self.assertEqual(len(self.store.history()),200)
            result=cleanup.clear_delivery_history(self.root)
            self.assertTrue(result['complete'],result)
            self.assertGreater(result['events'],350)
            self.assertEqual(self.store.history(),[])
            self.store.db.execute('UPDATE connector_deliveries SET confirmed_at=?,receipt=? WHERE token=?',(900,'new actual receipt',self.pending));self.store.db.commit()
            self.assertEqual(len(self.store.history()),1,'A pending token confirmed after cleanup must stay visible even after a clock adjustment')
        self.protected['connector_deliveries']=list(self.store.db.execute('SELECT * FROM connector_deliveries'))
        self.assert_protected()

    def test_only_confirmed_exact_receipt_exports_are_removed_and_operational_audit_stays(self):
        removed=[]
        for prefix in ('codex-receipt-','codex-local-receipt-'):
            path=self.logs/(prefix+self.confirmed+'.json');path.write_bytes(b'Confirmed exported payload');removed.append(path)
            path=self.logs/(prefix+self.pending+'.json');path.write_bytes(b'Pending readback');self.files[path]=path.read_bytes()
        arbitrary=self.logs/'do-not-touch.json';arbitrary.write_bytes(b'Unknown log');self.files[arbitrary]=arbitrary.read_bytes()
        records=[{'action':'confirmed','token':self.confirmed,'receipt_file':'../../approval-config.json'},
                 {'action':'awaiting_receipt','token':self.pending}, {'action':'error','reason':'Operational error'}, {'token':['malformed']}]
        audit=self.logs/'codex-delivery-events.jsonl';audit.write_bytes(b''.join((json.dumps(row)+'\n').encode() for row in records));size=audit.stat().st_size
        result=cleanup.clear_delivery_history(self.root)
        self.assertTrue(result['complete'],result);self.assertEqual(result['files'],2);self.assertEqual(result['audit_events'],1)
        self.assertTrue(all(not path.exists() for path in removed))
        after=[json.loads(line) for line in audit.read_text().splitlines()]
        self.assertEqual(after[0],{'type':'cleared'});self.assertEqual(after[1:],records[1:]);self.assertEqual(audit.stat().st_size,size)
        self.assert_protected()

    def test_notification_payloads_are_overwritten_without_changing_offsets_or_operational_lines(self):
        old={'type':'notification','created':'1970-01-01T00:01:00+00:00','title':'PRIVATE È','body':'PRIVATE BODY'}
        future={'type':'notification','created':'1970-01-01T00:20:00Z','title':'New after click','body':'Keep new'}
        status={'type':'status','state':'error','message':'Keep runtime state'}
        lines=[(json.dumps(old,ensure_ascii=False)+'\r\n').encode(),(json.dumps(status)+'\n').encode(),(json.dumps(future)+'\n').encode(),b'not valid JSON\n',b'{"type":"notification","body":"still writing']
        path=self.logs/'events.jsonl';before=b''.join(lines);path.write_bytes(before)
        with patch('history_cleanup.time.time',return_value=1000):result=cleanup.clear_notifications(self.root)
        self.assertTrue(result['complete'],result);self.assertEqual(result['notifications'],1);self.assertEqual(result['cleared_at'],1000)
        after=path.read_bytes();self.assertEqual(len(after),len(before));self.assertNotIn(b'PRIVATE',after)
        self.assertEqual(after[len(lines[0]):],before[len(lines[0]):]);self.assertTrue(after[:len(lines[0])].endswith(b'\r\n'))
        self.assertEqual(json.loads(after[:len(lines[0])]),{'type':'cleared'})
        self.assertEqual(json.loads((self.logs/cleanup.NOTIFICATION_MARKER).read_text()),{'cleared_at':1000})
        self.assert_protected()

    def test_notification_append_after_snapshot_survives_and_does_not_replay_preclear_data(self):
        path=self.logs/'events.jsonl';old=(json.dumps({'type':'notification','title':'Old pending card'})+'\n').encode();path.write_bytes(old)
        original=cleanup._redact
        extra=(json.dumps({'type':'notification','created':1200,'title':'New pending card'})+'\n').encode()
        def append_then_redact(target,initial,predicate,deadline=None):
            with target.open('ab') as stream:stream.write(extra)
            return original(target,initial,predicate,deadline)
        with patch('history_cleanup.time.time',return_value=1000),patch('history_cleanup._redact',side_effect=append_then_redact):
            result=cleanup.clear_notifications(self.root)
        self.assertTrue(result['complete'],result)
        with path.open('rb') as stream:stream.seek(len(old));self.assertEqual(stream.read(),extra)
        self.assertEqual(json.loads(path.read_bytes()[:len(old)]),{'type':'cleared'})

    def test_unsafe_hardlinked_notification_file_is_preserved_without_marker(self):
        outside=Path(self.temporary.name)/'outside.jsonl';outside.write_bytes(b'{"type":"notification","body":"Keep shared"}\n')
        os.link(outside,self.logs/'events.jsonl')
        result=cleanup.clear_notifications(self.root)
        self.assertFalse(result['complete']);self.assertEqual(outside.read_bytes(),b'{"type":"notification","body":"Keep shared"}\n')
        self.assertFalse((self.logs/cleanup.NOTIFICATION_MARKER).exists())

    def test_unsafe_hardlinked_database_is_not_modified(self):
        self.store.db.close()
        outside=Path(self.temporary.name)/'shared.sqlite3';os.link(self.logs/'messages.sqlite3',outside)
        before=outside.read_bytes();result=cleanup.clear_delivery_history(self.root)
        self.assertFalse(result['complete']);self.assertEqual(outside.read_bytes(),before)
        self.store.db=sqlite3.connect(self.logs/'messages.sqlite3')

    def test_export_hardlink_is_not_unlinked_and_is_reported(self):
        outside=Path(self.temporary.name)/'receipt.json';outside.write_bytes(b'Keep shared receipt')
        target=self.logs/('codex-receipt-'+self.confirmed+'.json');os.link(outside,target)
        result=cleanup.clear_delivery_history(self.root)
        self.assertFalse(result['complete']);self.assertTrue(target.exists());self.assertEqual(outside.read_bytes(),b'Keep shared receipt')
        self.assert_protected()

    def test_reparse_logs_are_rejected_before_any_change(self):
        original=Path.lstat
        def fake_reparse(path,*args,**kwargs):
            info=original(path,*args,**kwargs)
            if path==self.logs:return SimpleNamespace(st_mode=stat.S_IFDIR,st_file_attributes=getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',1024))
            return info
        with patch.object(Path,'lstat',fake_reparse):
            self.assertFalse(cleanup.clear_delivery_history(self.root)['complete'])
            self.assertFalse(cleanup.clear_notifications(self.root)['complete'])
        self.assert_protected()

    def test_corrupt_or_hardlinked_marker_does_not_hide_uncleared_notifications(self):
        path=self.logs/'events.jsonl';payload=b'{"type":"notification","body":"Keep until cleanup succeeds"}\n';path.write_bytes(payload)
        marker=self.logs/cleanup.NOTIFICATION_MARKER;marker.write_text('[]')
        self.assertFalse(cleanup.clear_notifications(self.root)['complete']);self.assertEqual(path.read_bytes(),payload)
        marker.unlink();outside=Path(self.temporary.name)/'marker.json';outside.write_text('{"cleared_at":1}');os.link(outside,marker)
        self.assertFalse(cleanup.clear_notifications(self.root)['complete']);self.assertEqual(path.read_bytes(),payload)
        self.assertEqual(outside.read_text(),'{"cleared_at":1}')

    def test_missing_profile_does_not_create_files_and_cleanup_is_repeatable(self):
        missing=self.root/'absent'
        self.assertTrue(cleanup.clear_notifications(missing)['complete']);self.assertFalse(missing.exists())
        cleanup.clear_delivery_history(self.root);again=cleanup.clear_delivery_history(self.root)
        self.assertTrue(again['complete'],again);self.assertEqual(again['events'],0);self.assertEqual(again['files'],0)
        self.assert_protected()

    def test_running_supervisor_keeps_offset_and_queues_only_new_notification_after_clear(self):
        import question_supervisor
        config={'away_mode':True,'paused':True,'apps':{'codex':{'questions_enabled':True}}}
        path=self.root/'approval-config.json';path.write_text(json.dumps(config),encoding='utf-8');self.files[path]=path.read_bytes()
        event_path=self.logs/'events.jsonl'
        old={'type':'notification','app_id':'OpenAI.Codex_QA!App','title':'Old cleared target','body':'has a question'}
        event_path.write_text(json.dumps(old)+'\n',encoding='utf-8')
        states=[]
        class Finished(Exception):pass
        def publish(state):
            states.append(state)
            if len(states)==1:
                with patch('history_cleanup.time.time',return_value=1000):self.assertTrue(cleanup.clear_notifications(self.root)['complete'])
                new={**old,'title':'New target','created':1200}
                with event_path.open('a',encoding='utf-8') as stream:stream.write(json.dumps(new)+'\n')
            else:raise Finished()
        with patch.object(question_supervisor,'ROOT',self.root),patch.object(question_supervisor,'LOGS',self.logs), \
                patch.object(question_supervisor,'load_jobs',return_value=[]),patch.object(question_supervisor,'publish',side_effect=publish), \
                patch.object(question_supervisor.time,'sleep'),patch.object(question_supervisor,'bounded_worker') as worker:
            with self.assertRaises(Finished):question_supervisor.run()
            worker.assert_not_called()
        self.assertEqual([state['queued'] for state in states],[0,1])
        self.assert_protected()
