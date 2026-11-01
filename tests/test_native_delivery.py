import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from unittest.mock import Mock
import uuid

from delivery_journal import Journal,digest
from message_engine import TimerStore
from native_delivery import cycle,worker_lock,session_authority,work_due
from native_receipts import claude_receipt,codex_page_receipt
from native_transports import ClaudeTransport,CodexTransport,NotDispatched,ChatBusy,codex_executable


class NativeDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.store=TimerStore(self.root/'messages.sqlite3')
        self.job={'id':'job','version':'1','app':'codex','thread_id':str(uuid.uuid4()),
            'title':'Original','group':'Tests','message':'Testo letterale\nà <>&',
            'interval_seconds':60,'activated_at':1000,'enabled':True,
            'send_when_present':True,'send_when_active':True}
        self.jobs=[self.job];self.config={'paused':False,'idle_seconds_before_actions':30}
        self.sent=[];self.closed=0;self.prepare_hook=lambda:None;self.raise_send=False;self.accepted=True
        outer=self
        class FakeTransport:
            def __init__(self,job,config,root):
                self.binding={'app':job['app'],'logical_id':job['thread_id'],'runtime_id':job['thread_id']}
            def prepare(self,attempt):outer.prepare_hook()
            def dispatch(self,attempt):
                outer.sent.append(attempt['id'])
                if outer.raise_send:raise TimeoutError('ACK perso')
                return {'accepted':outer.accepted,'detail':'Accodato' if outer.accepted else 'Consegna non confermata'}
            def close(self):outer.closed+=1
        self.factory=FakeTransport
    def tearDown(self):self.store.db.close();self.tmp.cleanup()
    def step(self,at=1061,recover=None):
        return cycle(self.store,self.root,lambda:self.config,lambda:self.jobs,self.factory,at,recover)
    def test_pc_present_allowed_without_idle_observation(self):
        with patch('automation_policy.idle_seconds',side_effect=AssertionError('No global idle gate')):
            self.assertEqual('accepted',self.step()['action'])
        self.assertEqual(1,len(self.sent));self.assertEqual(1,self.closed)
    def test_pc_present_off_defers_without_creating_intent(self):
        self.job['send_when_present']=False
        with patch('automation_policy.idle_seconds',return_value=0):self.step()
        self.assertEqual([],self.sent)
        self.assertEqual(0,self.store.db.execute('SELECT count(*) FROM native_attempts').fetchone()[0])

    def test_legacy_quarantine_on_old_version_is_visible_without_resending(self):
        self.store.db.execute('CREATE TABLE connector_deliveries(token TEXT,job_id TEXT,thread_id TEXT,confirmed_at REAL)')
        self.store.db.execute('INSERT INTO connector_deliveries VALUES(?,?,?,NULL)',
            ('old-token','old-alias',self.job['thread_id']))
        self.store.db.commit()
        result=self.step()
        self.assertEqual('legacy_pending',result['results'][0]['action'])
        self.assertEqual([],self.sent)
        self.assertIn('Gestisci invii incerti',self.store.rows()[0]['detail'])
        self.assertEqual(1060,self.store.rows()[0]['due'])
    def test_pause_during_prepare_cancels_before_effect(self):
        self.prepare_hook=lambda:self.config.update(paused=True)
        self.assertEqual('paused',self.step()['action']);self.assertEqual([],self.sent)
        self.assertEqual(1060,self.store.rows()[0]['due'])
    def test_version_edit_during_prepare_cannot_send_stale_text(self):
        self.prepare_hook=lambda:setattr(self,'jobs',[{**self.job,'version':'2','message':'Altro'}])
        self.assertEqual('paused',self.step()['action']);self.assertEqual([],self.sent)
    def test_rename_preserves_authorization_by_id(self):
        self.prepare_hook=lambda:setattr(self,'jobs',[{**self.job,'title':'Rinominata'}])
        self.assertEqual('accepted',self.step()['action'])
    def test_lost_ack_cannot_send_a_second_time_even_paused_or_deleted(self):
        self.raise_send=True;self.step();token=self.sent[0]
        self.config['paused']=True;self.jobs=[]
        calls=[]
        def recover(row,journal,config,root):calls.append(row['id']);return {'action':'awaiting_receipt'}
        self.step(9999999999,recover)
        self.assertEqual([token],self.sent);self.assertEqual([token],calls)
        self.assertIsNone(self.store.rows()[0]['last_sent'])
    def test_unknown_does_not_starve_a_different_target(self):
        self.raise_send=True;self.step();self.raise_send=False
        self.jobs.append({**self.job,'id':'two','thread_id':str(uuid.uuid4())})
        self.step(9999999999,lambda *args:{'action':'awaiting_receipt'})
        self.assertEqual(2,len(self.sent));self.assertNotEqual(self.sent[0],self.sent[1])
    def test_reserved_crash_has_proof_of_no_effect_and_can_retry(self):
        self.store.prepare(self.job,1000);journal=Journal(self.store)
        old=journal.reserve(self.job,{'app':'codex','logical_id':self.job['thread_id'],'runtime_id':self.job['thread_id']},1061)
        self.step()
        self.assertEqual('cancelled_before_dispatch',journal.get(old['id'])['state'])
        self.assertEqual(1,len(self.sent));self.assertNotEqual(old['id'],self.sent[0])
    def test_live_os_lock_is_not_taken_over(self):
        with worker_lock(self.root/'native.lock') as first:
            with worker_lock(self.root/'native.lock') as second:
                self.assertTrue(first);self.assertFalse(second)

    def test_negative_delivery_result_is_not_presented_as_accepted(self):
        self.accepted=False
        result=self.step()
        self.assertEqual('awaiting_receipt',result['action'])
        self.assertEqual('unknown',Journal(self.store).get(self.sent[0])['state'])

    def test_gui_closure_during_preparation_prevents_send(self):
        alive=[True]
        self.prepare_hook=lambda:alive.__setitem__(0,False)
        result=cycle(self.store,self.root,lambda:self.config,lambda:self.jobs,self.factory,1061,authorized=lambda:alive[0])
        self.assertEqual('paused',result['action']);self.assertEqual([],self.sent)

    def test_pending_receipts_wake_worker_without_any_current_job(self):
        self.step()
        self.assertTrue(work_due([],self.root/'messages.sqlite3',9999999999))

    def test_exit_intent_overrides_live_heartbeat_before_process_lookup(self):
        logs=self.root/'logs';logs.mkdir()
        (logs/'gui-heartbeat.json').write_text(json.dumps({'run_id':'old','pid':123,'updated_at':1000}))
        (logs/'exit-intent.json').write_text(json.dumps({'run_id':'old'}))
        with patch('psutil.Process',side_effect=AssertionError('No process query after exit')):
            self.assertFalse(session_authority(self.root,'old',1001))
            self.assertFalse(session_authority(self.root,'new',1001))

    def test_missing_claude_module_cannot_fall_through_to_a_model_prompt(self):
        client=Mock()
        client.receive_until.return_value={'response':{'subtype':'success','response':{
            'commands':[],'current_permission_mode':'default'}}}
        native=ClaudeTransport.__new__(ClaudeTransport)
        native.root=self.root;native.executable='owned-cli';native.client=None
        attempt={'id':str(uuid.uuid4()),'binding_json':json.dumps({'runtime_id':'test'})}
        with patch('bounded_process.run_bounded',return_value={'returncode':0,'timed_out':False}), \
             patch('native_transports.OwnedJsonProcess',return_value=client):
            with self.assertRaisesRegex(RuntimeError,'nessun messaggio a un modello'):native.prepare(attempt)
        self.assertEqual(1,client.send.call_count)
        self.assertEqual('control_request',client.send.call_args.args[0]['type'])

    def test_claude_preflight_requires_allow_without_preapproving_tools(self):
        for decision,inbound in (('allow',True),('ask',True),('deny',True),('allow',False)):
            with self.subTest(decision=decision,inbound=inbound):
                client=Mock()
                client.receive_until.side_effect=[
                    {'response':{'subtype':'success','response':{'commands':[{'name':'sentinella-status'},{'name':'sentinella-send'}],
                        'current_permission_mode':'default'}}},
                    {'type':'system','subtype':'init','permissionMode':'default'},
                    {'type':'result','num_turns':0,'result':'SENTINELLA_JSON:'+json.dumps({
                        'ready':True,'revision':1,'inbound_refused':inbound,'permission_allowed':decision=='allow','permission_decision':decision})}]
                native=ClaudeTransport.__new__(ClaudeTransport)
                native.root=self.root;native.executable='owned-cli';native.client=None;native.job=self.job
                attempt={'id':str(uuid.uuid4()),'binding_json':json.dumps({'runtime_id':self.job['thread_id']})}
                with patch('bounded_process.run_bounded',return_value={'returncode':0,'timed_out':False}), \
                     patch('native_transports.OwnedJsonProcess',return_value=client) as process:
                    if not inbound:
                        with self.assertRaisesRegex(RuntimeError,'non è isolato'):native.prepare(attempt)
                    elif decision=='allow':native.prepare(attempt)
                    else:
                        with self.assertRaises(NotDispatched):native.prepare(attempt)
                args=process.call_args.args[0]
                self.assertEqual('SendMessage',args[args.index('--tools')+1])
                self.assertEqual({'crossSessionInbound':'refuse'},json.loads(args[args.index('--settings')+1]))
                self.assertNotIn('--allowedTools',args)
                user=[call.args[0]['message']['content'] for call in client.send.call_args_list if call.args[0].get('type')=='user']
                self.assertEqual(1,len(user));self.assertTrue(user[0].startswith('/sentinella-status '))

    def test_codex_rechecks_authority_after_readiness_before_queue_effect(self):
        native=CodexTransport.__new__(CodexTransport);native.client=Mock()
        native.job={'send_when_active':True}
        native.client.rpc.return_value={'data':[]};native.guard=lambda:False
        with self.assertRaises(NotDispatched):native.dispatch({})
        self.assertEqual(['thread/loaded/list'],[call.args[0] for call in native.client.rpc.call_args_list])

    def test_missing_cli_never_starts_a_gui_alias_from_path(self):
        desktop=Mock();desktop.info={'exe':str(self.root/'Codex.exe')}
        with patch('native_transports.receiver_processes',return_value=[desktop]), \
             patch('shutil.which',return_value=str(self.root/'WindowsApps/codex.exe')) as path_alias, \
             patch('native_transports.OwnedJsonProcess') as process:
            with self.assertRaisesRegex(RuntimeError,'CLI Codex compatibile non disponibile'):
                CodexTransport(self.job,self.config,self.root)
        path_alias.assert_not_called();process.assert_not_called()

    def test_receipt_recovery_uses_saved_cli_or_current_bundle_only(self):
        saved=self.root/'saved-cli.exe';saved.write_bytes(b'previously-qualified-fixture')
        with patch('native_transports.receiver_processes',side_effect=AssertionError('Bound CLI already known')):
            self.assertEqual(str(saved),codex_executable(self.config,{'cli_executable':str(saved)}))
        saved.unlink()
        with patch('native_transports.receiver_processes',return_value=[]), \
             patch('native_transports.OwnedJsonProcess') as process:
            with self.assertRaisesRegex(RuntimeError,'CLI Codex compatibile non disponibile'):
                CodexTransport(self.job,self.config,self.root,readonly=True,binding={'cli_executable':str(saved)})
        process.assert_not_called()

    def test_reconcile_only_never_creates_a_due_attempt(self):
        result=cycle(self.store,self.root,lambda:self.config,lambda:self.jobs,self.factory,1061,reconcile_only=True)
        self.assertEqual('idle',result['action']);self.assertEqual([],self.sent)

    def test_claude_owner_discovery_only_reads_desktop_children(self):
        runtime=str(uuid.uuid4());history=self.root/'history.jsonl';history.write_text('')
        parent=Mock();owner=Mock();owner.pid=123
        parent.children.return_value=[owner]
        owner.cmdline.return_value=['owned-cli','--resume='+runtime,'--permission-mode','acceptEdits']
        owner.exe.return_value='owned-cli'
        identity={'session':runtime,'index':str(self.root/'index.json'),'history':str(history)}
        job={**self.job,'app':'claude','session_id':str(uuid.uuid4())}
        with patch('native_transports.receiver_processes',return_value=[parent]), \
             patch('claude_delivery.locate',return_value=identity), \
             patch('native_transports.psutil.process_iter',side_effect=AssertionError('No global PPID scan')):
            native=ClaudeTransport(job,{},self.root)
        self.assertEqual(runtime,native.binding['runtime_id'])
        parent.children.assert_called_once()

    def test_busy_chat_defers_without_fault_or_due_reset(self):
        result=cycle(self.store,self.root,lambda:self.config,lambda:self.jobs,
            factory=Mock(side_effect=ChatBusy('Chat in elaborazione: attendo che sia libera')),now=1061)
        self.assertEqual('waiting_for_chat',result['results'][0]['action'])
        self.assertEqual(0,self.store.db.execute('SELECT count(*) FROM message_failures').fetchone()[0])
        self.assertEqual((1060,'idle'),tuple(self.store.db.execute('SELECT due,phase FROM timers').fetchone()))
        self.assertEqual(0,self.store.db.execute('SELECT count(*) FROM native_attempts').fetchone()[0])


class NativeReceiptTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'history.jsonl'
        self.path.write_text('',encoding='utf-8')
        self.token=str(uuid.uuid4());self.target=str(uuid.uuid4());self.physical=str(uuid.uuid4())
        self.text='Testo identico\ncon à e spazi '
        self.binding={'runtime_id':self.target,'history':str(self.path),'history_offset':0,'sender_name':'sentinella-'+self.token}
        self.attempt={'id':self.token,'created':1000,'message_hash':digest(self.text),'binding_json':json.dumps(self.binding)}
        self.record={'type':'user','message':{'role':'user','content':'Attribuzione nativa'},
            'uuid':self.physical,'timestamp':'1970-01-01T00:16:41+00:00','sessionId':self.target,
            'entrypoint':'claude-desktop','turnOrigin':'peer','origin':{'kind':'peer','plugin':'sentinella-bridge',
            'name':self.binding['sender_name'],'body':self.text,'msg_id':str(uuid.uuid4()),'fromMode':'prompting'}}
    def tearDown(self):self.tmp.cleanup()
    def write(self,*records):self.path.write_text(''.join(json.dumps(r)+'\n' for r in records),encoding='utf-8')
    def test_real_peer_metadata_preserves_exact_body(self):
        self.write(self.record);receipt,cursor=claude_receipt(self.attempt,1002)
        self.assertEqual(self.physical,receipt['physical_id']);self.assertEqual(self.path.stat().st_size,cursor)
    def test_manual_identical_text_and_another_attempt_cannot_confirm(self):
        for key,value in [('turnOrigin','human'),('sessionId',str(uuid.uuid4())),('type','assistant')]:
            with self.subTest(key=key):
                self.write({**self.record,key:value});self.assertIsNone(claude_receipt(self.attempt,1002)[0])
        self.write({**self.record,'origin':{**self.record['origin'],'name':'sentinella-'+str(uuid.uuid4())}})
        self.assertIsNone(claude_receipt(self.attempt,1002)[0])
    def test_partial_record_cursor_waits_and_then_recovers(self):
        raw=json.dumps(self.record);self.path.write_text(raw[:80],encoding='utf-8')
        self.assertEqual((None,0),claude_receipt(self.attempt,1002))
        self.path.write_text(raw+'\n',encoding='utf-8');self.assertIsNotNone(claude_receipt(self.attempt,1002)[0])
    def test_truncated_history_is_not_a_negative_receipt(self):
        self.binding['history_offset']=100;self.attempt['binding_json']=json.dumps(self.binding)
        with self.assertRaises(ValueError):claude_receipt(self.attempt,1002)

    def test_large_unrelated_output_does_not_hide_the_receipt(self):
        self.write({'type':'assistant','message':{'content':'x'*(3*1024*1024)}},self.record)
        receipt,cursor=claude_receipt(self.attempt,1002)
        self.assertEqual(self.physical,receipt['physical_id'])
        self.assertEqual(self.path.stat().st_size,cursor)

    def test_scan_budget_advances_past_one_large_record_between_cycles(self):
        self.write({'type':'assistant','message':{'content':'x'*(9*1024*1024)}},self.record)
        receipt,cursor=claude_receipt(self.attempt,1002)
        self.assertIsNone(receipt);self.assertGreater(cursor,8*1024*1024)
        self.binding['history_offset']=cursor;self.attempt['binding_json']=json.dumps(self.binding)
        self.assertEqual(self.physical,claude_receipt(self.attempt,1002)[0]['physical_id'])

    def test_oversized_or_incomplete_record_is_never_consumed_as_a_receipt(self):
        self.path.write_bytes(b'x'*(16*1024*1024))
        with self.assertRaisesRegex(ValueError,'troppo grande'):claude_receipt(self.attempt,1002)
        self.path.write_bytes(b'{"type":"assistant","message":"'+b'x'*(3*1024*1024))
        self.assertEqual((None,0),claude_receipt(self.attempt,1002))

    def test_malformed_unrelated_data_is_ignored_but_correlated_date_is_rejected(self):
        self.write([],{'type':'assistant','origin':None},self.record)
        self.assertEqual(self.physical,claude_receipt(self.attempt,1002)[0]['physical_id'])
        for value in (None,1,'invalid','1970-01-01T00:16:41'):
            with self.subTest(value=value):
                self.write({**self.record,'timestamp':value})
                with self.assertRaisesRegex(ValueError,'Data della ricevuta'):claude_receipt(self.attempt,1002)
    def test_two_native_receipts_for_one_attempt_raise(self):
        self.write(self.record,{**self.record,'uuid':str(uuid.uuid4())})
        with self.assertRaises(ValueError):claude_receipt(self.attempt,1002)
    def test_body_changed_by_recipient_is_not_confirmed(self):
        self.write({**self.record,'origin':{**self.record['origin'],'body':self.text.rstrip()}})
        with self.assertRaises(ValueError):claude_receipt(self.attempt,1002)
    def test_codex_requires_client_id_exact_content_and_native_timestamp(self):
        entry={'item':{'type':'userMessage','id':self.physical,'clientId':self.token,
            'content':[{'type':'text','text':self.text}]},'startedAtMs':1001000}
        self.assertEqual(self.physical,codex_page_receipt(self.attempt,{'data':[entry]},1002)['physical_id'])
        entry['item']['clientId']=str(uuid.uuid4())
        self.assertIsNone(codex_page_receipt(self.attempt,{'data':[entry]},1002))


if __name__=='__main__':unittest.main()
