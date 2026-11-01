import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
import threading
import uuid
import copy

from delivery_journal import Journal, digest
from message_engine import TimerStore


class DeliveryJournalTests(unittest.TestCase):
    def codex_appended_receipt(self):
        from native_receipts import codex_page_receipt
        target=str(uuid.uuid4())
        job={**self.job,'app':'codex','version':'codex-v1','thread_id':target}
        binding={'app':'codex','logical_id':target,'runtime_id':target,'server_version':'sentinella/0.166.0-alpha.5.1'}
        self.store.prepare(job,1000)
        row=self.journal.reserve(job,binding,1061)
        self.journal.transition(row['id'],'dispatch_intent','Invio',1062)
        row=self.journal.get(row['id'])
        entry={'item':{'type':'userMessage','id':str(uuid.uuid4()),'clientId':row['id'],
                      'content':[{'type':'text','text':job['message']+'\n'}]},'startedAtMs':1063000}
        return row,codex_page_receipt(row,{'data':[entry]},1064)

    def test_confirm_checks_the_real_appended_lf_and_records_format_audit(self):
        row,receipt=self.codex_appended_receipt()
        self.assertNotEqual(row['message_hash'],receipt['message_hash'])
        self.assertTrue(self.journal.confirm(row['id'],receipt,1064))
        self.assertEqual((1063,1123,'idle'),tuple(self.store.db.execute('SELECT last_sent,due,phase FROM timers').fetchone()))
        evidence=json.loads(self.store.db.execute('SELECT evidence_json FROM native_receipts').fetchone()[0])
        self.assertEqual(digest(self.job['message']+'\n'),evidence['message_hash'])
        self.assertEqual(row['message_hash'],evidence['expected_message_hash'])
        self.assertIn('nuova riga finale',self.journal.get(row['id'])['detail'])

    def test_forged_format_marker_or_native_item_cannot_advance_the_timer(self):
        row,receipt=self.codex_appended_receipt()
        for field in ('content','clientId','id'):
            with self.subTest(field=field):
                altered=copy.deepcopy(receipt)
                altered['native_item']['item'][field]=([{'type':'text','text':'changed'}] if field=='content' else str(uuid.uuid4()))
                with self.assertRaises(ValueError):self.journal.confirm(row['id'],altered,1064)
        altered={**receipt,'native_item':{}}
        with self.assertRaises(ValueError):self.journal.confirm(row['id'],altered,1064)
        self.assertEqual(0,self.store.db.execute('SELECT count(*) FROM native_receipts').fetchone()[0])
        self.assertIsNone(self.store.rows()[0]['last_sent'])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name)/'messages.sqlite3'
        self.store = TimerStore(self.path)
        self.journal = Journal(self.store)
        self.job = {'id':'job','version':'v1','app':'claude','session_id':'desktop-id',
                    'title':'Chat','group':'Prove','message':'Testo identico',
                    'activated_at':1000,'interval_seconds':60,'enabled':True}
        self.binding = {'app':'claude','logical_id':'desktop-id','runtime_id':'cli-id'}
        self.store.prepare(self.job,1000)

    def tearDown(self):
        self.store.db.close()
        self.folder.cleanup()

    def attempt(self):
        return self.journal.reserve(self.job,self.binding,1061)

    def receipt(self, row, physical='physical-id', at=1063):
        if self.journal.get(row['id'])['state']=='reserved':
            self.journal.transition(row['id'],'dispatch_intent','Invio iniziato',1062)
        return {'app':'claude','target_id':'cli-id','message_hash':digest(self.job['message']),
                'physical_id':physical,'received_at':at,'correlation':row['id']}

    def test_claim_and_timer_are_one_commit(self):
        row = self.attempt()
        self.assertEqual('reserved',row['state'])
        self.assertEqual('uncertain',self.store.rows()[0]['phase'])
        self.assertIsNone(self.attempt())
        self.assertEqual(1,self.store.db.execute('SELECT count(*) FROM native_attempts').fetchone()[0])

    def test_failure_between_claim_and_timer_rolls_back_both(self):
        self.store.db.execute("CREATE TRIGGER fail_update BEFORE UPDATE ON timers BEGIN SELECT RAISE(ABORT,'injected'); END")
        with self.assertRaises(sqlite3.DatabaseError): self.attempt()
        self.assertEqual(0,self.store.db.execute('SELECT count(*) FROM native_attempts').fetchone()[0])
        self.assertEqual('idle',self.store.rows()[0]['phase'])

    def test_ack_is_not_a_receipt_or_timer_advance(self):
        row=self.attempt()
        self.journal.transition(row['id'],'dispatch_intent','Invio iniziato',1062)
        self.journal.transition(row['id'],'accepted','Accodato',1062,'queue-id')
        timer=self.store.rows()[0]
        self.assertIsNone(timer['last_sent'])
        self.assertEqual(1060,timer['due'])

    def test_confirm_updates_attempt_receipt_and_timer_atomically(self):
        row=self.attempt()
        self.store.fail(self.job,'Errore precedente alla consegna',1061)
        self.assertTrue(self.journal.confirm(row['id'],self.receipt(row),1064))
        timer=self.store.rows()[0]
        self.assertEqual((1063,1123,'idle'),(timer['last_sent'],timer['due'],timer['phase']))
        self.assertEqual('received',self.journal.get(row['id'])['state'])
        self.assertEqual(1,self.store.db.execute('SELECT count(*) FROM native_receipts').fetchone()[0])
        self.assertIsNotNone(self.store.db.execute('SELECT resolved_at FROM message_failures').fetchone()[0])

    def test_lost_ack_is_reconciled_after_reopening_without_new_claim(self):
        row=self.attempt()
        self.journal.transition(row['id'],'dispatch_intent','Invio iniziato',1062)
        self.store.db.close()
        self.store=TimerStore(self.path);self.journal=Journal(self.store)
        pending=self.journal.pending(1070)
        self.assertEqual([row['id']],[r['id'] for r in pending])
        self.journal.confirm(row['id'],self.receipt(row),1070)
        self.assertEqual(1,self.store.db.execute('SELECT count(*) FROM native_attempts').fetchone()[0])

    def test_receipt_is_terminal_even_when_callback_arrives_late(self):
        row=self.attempt();self.journal.confirm(row['id'],self.receipt(row),1064)
        self.assertFalse(self.journal.transition(row['id'],'accepted','Accodato',1070))
        self.assertFalse(self.journal.confirm(row['id'],self.receipt(row),1080))
        self.assertEqual(1123,self.store.rows()[0]['due'])

    def test_rejects_wrong_body_target_and_attempt(self):
        row=self.attempt()
        for key,value in [('target_id','other'),('message_hash','wrong'),('correlation','other')]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.journal.confirm(row['id'],{**self.receipt(row),key:value},1064)
        self.assertEqual('dispatch_intent',self.journal.get(row['id'])['state'])

    def test_one_physical_receipt_cannot_confirm_two_identical_periodic_messages(self):
        first=self.attempt();self.journal.confirm(first['id'],self.receipt(first),1064)
        second=self.journal.reserve(self.job,self.binding,1124)
        with self.assertRaises(sqlite3.IntegrityError):
            self.journal.confirm(second['id'],self.receipt(second,at=1125),1126)
        self.assertEqual('dispatch_intent',self.journal.get(second['id'])['state'])
        self.assertEqual(1063,self.store.rows()[0]['last_sent'])

    def test_superseded_job_receipt_never_resets_new_version_timer(self):
        row=self.attempt()
        changed={**self.job,'version':'v2','activated_at':2000}
        self.store.prepare(changed,2000)
        self.journal.confirm(row['id'],self.receipt(row),2001)
        timer=self.store.rows()[0]
        self.assertEqual(('v2',2060,None),(timer['version'],timer['due'],timer['last_sent']))

    def test_pause_delete_do_not_hide_pending_receipt_work(self):
        row=self.attempt()
        self.assertEqual([row['id']],[r['id'] for r in self.journal.pending(1062)])
        self.assertEqual(self.job,json.loads(row['job_json']))

    def test_cancel_is_allowed_only_before_dispatch(self):
        row=self.attempt()
        self.journal.transition(row['id'],'dispatch_intent','Invio iniziato',1062)
        self.assertFalse(self.journal.transition(row['id'],'cancelled_before_dispatch','Annullato',1063))
        self.assertEqual('dispatch_intent',self.journal.get(row['id'])['state'])

    def test_cancelled_before_effect_can_retry_without_resetting_due(self):
        row=self.attempt()
        self.journal.transition(row['id'],'cancelled_before_dispatch','Pausa prima di ogni effetto',1062)
        new=self.journal.reserve(self.job,self.binding,1063)
        self.assertIsNotNone(new)
        self.assertNotEqual(row['id'],new['id'])
        self.assertEqual(1060,new['due'])

    def test_existing_uncertain_legacy_attempt_is_preserved(self):
        self.store.db.execute('CREATE TABLE connector_deliveries(token TEXT,job_id TEXT,confirmed_at REAL)')
        self.store.db.execute('INSERT INTO connector_deliveries VALUES(?,?,NULL)',('old','job'))
        self.store.db.commit()
        self.assertIsNone(self.attempt())
        self.assertEqual(1,self.store.db.execute('SELECT count(*) FROM connector_deliveries').fetchone()[0])

    def test_another_worker_cannot_claim_the_same_target(self):
        self.attempt()
        other={**self.job,'id':'other-job'};self.store.prepare(other,1000)
        other_store=TimerStore(self.path)
        try:
            other_journal=Journal(other_store)
            self.assertIsNone(other_journal.reserve(other,self.binding,1061))
        finally: other_store.db.close()

    def test_runtime_alias_cannot_open_a_second_attempt(self):
        self.attempt()
        job={**self.job,'id':'alias-job','session_id':'runtime-alias'}
        binding={**self.binding,'logical_id':'runtime-alias'}
        self.store.prepare(job,1000)
        self.assertIsNone(self.journal.reserve(job,binding,1061))

    def test_restarted_physical_session_cannot_bypass_old_logical_uncertainty(self):
        first=self.attempt()
        changed={**self.job,'version':'v2'}
        self.store.prepare(changed,1000)
        new_binding={**self.binding,'runtime_id':'new-cli-after-restart'}
        self.assertIsNone(self.journal.reserve(changed,new_binding,1061))
        self.assertEqual('reserved',self.journal.get(first['id'])['state'])
        self.assertEqual(1,self.store.db.execute('SELECT count(*) FROM native_attempts').fetchone()[0])

    def test_upgrade_keeps_duplicate_legacy_engines_reconcilable_without_new_effects(self):
        first=self.attempt()
        self.journal.transition(first['id'],'dispatch_intent','Old dispatch',1062)
        self.store.db.execute('DROP INDEX native_open_logical_target')
        self.store.db.execute('''INSERT INTO native_attempts
            (id,job_id,version,app,target_id,runtime_id,due,job_json,binding_json,message_hash,created,dispatched,state)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'unknown')''',
            ('old-duplicate','old-job','old','claude','desktop-id','old-cli-two',1060,json.dumps(self.job),
             json.dumps({**self.binding,'runtime_id':'old-cli-two'}),digest(self.job['message']),1061,1062))
        self.store.db.commit()
        upgraded=Journal(self.store)
        self.assertEqual(2,len(upgraded.pending(9999)))
        other={**self.job,'id':'new-alias'};self.store.prepare(other,1000)
        self.assertIsNone(upgraded.reserve(other,{**self.binding,'runtime_id':'third-cli'},1063))
        self.assertTrue(upgraded.confirm(first['id'],self.receipt(first),1064))
        self.assertEqual('unknown',upgraded.get('old-duplicate')['state'])
        Journal(self.store)
        self.assertTrue(self.store.db.execute("SELECT 1 FROM sqlite_master WHERE name='native_open_logical_target'").fetchone())
        self.assertEqual(2,self.store.db.execute('SELECT count(*) FROM native_attempts').fetchone()[0])

    def test_legacy_attempt_in_another_job_blocks_same_runtime(self):
        self.store.db.execute('CREATE TABLE claude_timer_deliveries(job_id TEXT,identity TEXT,stage TEXT)')
        self.store.db.execute('INSERT INTO claude_timer_deliveries VALUES(?,?,?)',
            ('old-job',json.dumps({'session':'cli-id'}),'sending'))
        self.store.db.commit()
        self.assertIsNone(self.attempt())

    def test_receipt_cannot_reopen_no_effect_terminal(self):
        row=self.attempt()
        receipt=self.receipt(row)
        self.journal.transition(row['id'],'rejected_before_dispatch','Nessun effetto',1063)
        with self.assertRaises(ValueError): self.journal.confirm(row['id'],receipt,1064)

    def test_simultaneous_workers_only_reserve_once(self):
        # Separate connections begin at the same barrier, not sequential calls.
        barrier=threading.Barrier(2)
        def claim(_):
            store=TimerStore(self.path)
            try:
                journal=Journal(store)
                barrier.wait(timeout=3)
                return journal.reserve(self.job,self.binding,1061)
            finally: store.db.close()
        with ThreadPoolExecutor(max_workers=2) as workers:
            rows=list(workers.map(claim,range(2)))
        self.assertEqual(1,sum(row is not None for row in rows))


if __name__=='__main__': unittest.main()
