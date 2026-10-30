"""Simulated desktop, real timer/ownership/receipt code and private SQLite files.

This proves protocol invariants under faults. It is not a real Claude delivery.
"""
from contextlib import ExitStack
from datetime import datetime,timezone
import itertools
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

from message_engine import TimerStore,run_cycle
from claude_delivery import Delivery


SESSION='11111111-2222-4333-8444-555555555555'


class Control:
    IsEnabled=True;IsOffscreen=False;AriaRole='';HasKeyboardFocus=False
    def __init__(self,kind,name='',value=''):
        self.ControlTypeName,self.Name,self.Value=kind,name,value
        self.parent=None;self.children=[]
    def GetRuntimeId(self):return [1,id(self)]
    def GetParentControl(self):return self.parent
    def GetValuePattern(self):return self
    def SetValue(self,value,**_):self.Value=value


class Harness:
    def __init__(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='SentinellaFault_')
        self.root=Path(self.temporary.name);(self.root/'logs').mkdir()
        self.history=self.root/'history.jsonl';self.history.write_text('')
        self.identity=dict(history=str(self.history),session='session',index='fixture-index')
        self.job=dict(id='fault-job',version='v1',app='claude',title='Test',group='Fixture',
                      message='Messaggio di prova: à, è, € e righe\nTesto esatto.',session_id=SESSION,
                      interval_seconds=60,activated_at=0,enabled=True,send_when_active=True,send_when_present=True)
        self.config=dict(paused=False,idle_seconds_before_actions=10,apps={'claude':{'process_path':'fixture.exe'}})
        self.now=0;self.idle=100;self.unlocked=True;self.clicks=0;self.mode='normal'
        self.doc=Control('DocumentControl','Test - Claude Code','https://claude.ai/epitaxy/local_'+SESSION)
        self.edit=Control('EditControl','Prompt')
        self.send=Control('ButtonControl','Invia')
        self.send.GetInvokePattern=lambda:self.send;self.send.Invoke=self.invoke
        self.composer=Control('GroupControl');self.composer.children=[self.edit,self.send]
        self.edit.parent=self.composer;self.send.parent=self.composer;self.composer.parent=self.doc
        self.controls=[self.doc,self.edit,self.send];self.windows=[self.controls]
        self.store=TimerStore(self.root/'logs/messages.sqlite3');self.store.prepare(self.job,0)
        self.stack=ExitStack()
        auto=SimpleNamespace(SetGlobalSearchTimeout=lambda _:None,ControlFromHandle=lambda handle:handle)
        for target,value in [('message_engine.ROOT',self.root),('automation_policy.ROOT',self.root)]:
            self.stack.enter_context(patch(target,value))
        self.stack.enter_context(patch.dict(sys.modules,{'uiautomation':auto}))
        self.stack.enter_context(patch('message_engine.load_jobs',side_effect=lambda:[dict(self.job)]))
        self.stack.enter_context(patch('chat_identity.resolve',side_effect=lambda job:dict(job)))
        self.stack.enter_context(patch('claude_delivery.locate',side_effect=lambda _:dict(self.identity)))
        self.stack.enter_context(patch('automation_policy.idle_seconds',side_effect=lambda:self.idle))
        self.stack.enter_context(patch('chat_desktop.idle_seconds',side_effect=lambda:self.idle))
        self.stack.enter_context(patch('chat_desktop.desktop_is_unlocked',side_effect=lambda:self.unlocked))
        self.stack.enter_context(patch('chat_desktop.app_window_handles',side_effect=lambda _:list(range(len(self.windows)))))
        self.stack.enter_context(patch('chat_desktop.native_controls',side_effect=lambda handle:self.windows[handle]))
        self.stack.enter_context(patch('chat_desktop.walk',side_effect=lambda node,**_:node.children))
        self.stack.enter_context(patch('time.time',side_effect=lambda:self.now))

    def close(self):
        self.stack.close();self.store.db.close();self.temporary.cleanup()

    def append(self,record):
        with self.history.open('a',encoding='utf-8') as stream:stream.write(json.dumps(record,ensure_ascii=False)+'\n')

    def invoke(self):
        self.clicks+=1
        if self.mode=='before':raise OSError('Provider failed before acknowledgement')
        text='Human edit after validation' if self.mode=='modified' else self.edit.Value
        if self.mode=='modified':self.edit.Value=text
        record=dict(type='user',uuid=str(uuid.uuid4()),sessionId='session',
                    timestamp=datetime.fromtimestamp(self.now+.001,timezone.utc).isoformat(),
                    message=dict(role='user',content=text))
        if self.mode=='assistant':record.update(type='assistant',message=dict(role='assistant',content=text))
        if self.mode=='wrong-session':record['sessionId']='another-session'
        if self.mode=='partial':
            data=json.dumps(record,ensure_ascii=False)+'\n';self.pending=data[len(data)//2:]
            with self.history.open('a',encoding='utf-8') as stream:stream.write(data[:len(data)//2])
        else:self.append(record)
        if self.mode=='after':raise OSError('Connection lost after native Invoke')

    def cycle(self,at):
        self.now=at
        (self.root/'approval-config.json').write_text(json.dumps(self.config),encoding='utf-8')
        return run_cycle(self.config,self.store,now=at)

    @property
    def state(self):return self.store.rows()[0]


class ReliabilityMatrixTests(unittest.TestCase):
    def make(self):
        harness=Harness();self.addCleanup(harness.close);return harness

    def test_real_protocol_repeats_multiple_slots_with_receipts_and_no_duplicate(self):
        h=self.make()
        for slot in range(4):
            at=h.state['due']+1
            self.assertEqual(h.cycle(at)['action'],'drafted')
            self.assertEqual(h.cycle(at+1)['action'],'awaiting_receipt')
            self.assertEqual(h.cycle(at+2)['action'],'draft_cleared')
            self.assertEqual(h.state['last_sent'],at+1)
            self.assertEqual(h.state['due'],at+61)
        self.assertEqual(h.clicks,4)
        self.assertEqual(h.store.db.execute("SELECT count(*) FROM message_events WHERE event='Consegnato con ricevuta'").fetchone()[0],4)

    def test_hundred_slots_multiple_windows_restart_and_transient_failure(self):
        h=self.make()
        for slot in range(100):
            # Accessory windows and an unrelated native chat never own this draft.
            other=Control('DocumentControl','Other - Claude Code','https://claude.ai/epitaxy/local_aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee')
            other_edit=Control('EditControl','Prompt','Unrelated draft');other_edit.parent=other
            h.windows=[[],[other,other_edit],h.controls,[]]
            at=h.state['due']+1
            if slot%10==0:
                previous_sent=h.state['last_sent']
                h.windows=[]
                self.assertEqual(h.cycle(at)['action'],'error')
                self.assertEqual(h.state['last_sent'],previous_sent)
                h.windows=[[],[other,other_edit],h.controls,[]]
                at+=121
            self.assertEqual(h.cycle(at)['action'],'drafted')
            self.assertEqual(h.cycle(at+1)['action'],'awaiting_receipt')
            if slot%7==0:
                h.store.db.close();h.store=TimerStore(h.root/'logs/messages.sqlite3')
            self.assertEqual(h.cycle(at+2)['action'],'draft_cleared')
            self.assertEqual(h.state['last_sent'],at+1)
            self.assertEqual(h.clicks,slot+1)
            self.assertEqual(other_edit.Value,'Unrelated draft')
        self.assertEqual(h.store.db.execute("SELECT count(*) FROM message_events WHERE event='Consegnato con ricevuta'").fetchone()[0],100)

    def test_activation_presence_draft_and_window_matrix_preserves_safety(self):
        # 64 combinations, each with independent storage and an immutable oracle.
        for paused,enabled,present,allow_present,foreign,available in itertools.product((False,True),repeat=6):
            with self.subTest(paused=paused,enabled=enabled,present=present,allow_present=allow_present,foreign=foreign,available=available):
                h=Harness()
                try:
                    h.config['paused']=paused;h.job.update(enabled=enabled,send_when_present=allow_present)
                    h.idle=0 if present else 100;h.windows=[h.controls] if available else []
                    h.edit.Value='Human draft' if foreign else ''
                    for at in (61,62,63):h.cycle(at)
                    permitted=not paused and enabled and (not present or allow_present) and not foreign and available
                    self.assertEqual(h.clicks,1 if permitted else 0)
                    if foreign:self.assertEqual(h.edit.Value,'Human draft')
                    if not permitted:self.assertIsNone(h.state['last_sent'])
                finally:h.close()

    def test_app_disappears_after_preparation_and_recovers_without_losing_draft(self):
        h=self.make();h.cycle(61);owned=h.edit.Value
        h.windows=[];self.assertEqual(h.cycle(62)['action'],'error')
        self.assertEqual(h.edit.Value,owned);self.assertEqual(h.clicks,0)
        h.windows=[h.controls]
        self.assertEqual(h.cycle(68)['action'],'awaiting_receipt')
        self.assertEqual(h.cycle(69)['action'],'draft_cleared')
        self.assertEqual(h.clicks,1)

    def test_provider_error_after_click_is_reconciled_by_receipt_without_resend(self):
        h=self.make();h.mode='after';h.cycle(61)
        self.assertEqual(h.cycle(62)['action'],'error')
        self.assertEqual(h.state['phase'],'uncertain')
        h.cycle(63);h.cycle(64)
        self.assertEqual(h.clicks,1);self.assertEqual(h.state['last_sent'],62)
        self.assertNotIn('delivery_error',h.state)

    def test_unknown_outcome_before_acknowledgement_never_retries_a_click(self):
        h=self.make();h.mode='before';h.cycle(61);h.cycle(62)
        for at in (63,100,1000):h.cycle(at)
        self.assertEqual(h.clicks,1);self.assertEqual(h.state['phase'],'uncertain')
        self.assertIsNone(h.state['last_sent'])

    def test_partial_receipt_is_completed_later_without_any_new_send(self):
        h=self.make();h.mode='partial';h.cycle(61);h.cycle(62);h.cycle(63)
        self.assertIsNone(h.state['last_sent'])
        with h.history.open('a',encoding='utf-8') as stream:stream.write(h.pending)
        h.cycle(64)
        self.assertEqual(h.clicks,1);self.assertEqual(h.state['last_sent'],62)

    def test_assistant_text_or_other_session_is_never_a_receipt(self):
        for mode in ('assistant','wrong-session'):
            with self.subTest(mode=mode):
                h=Harness()
                try:
                    h.mode=mode;h.cycle(61);h.cycle(62);h.cycle(63)
                    self.assertIsNone(h.state['last_sent']);self.assertEqual(h.clicks,1)
                finally:h.close()

    def test_human_edit_during_submission_is_not_claimed_as_configured_delivery(self):
        h=self.make();h.mode='modified';h.cycle(61);h.cycle(62);h.cycle(63)
        self.assertIsNone(h.state['last_sent']);self.assertEqual(h.clicks,1)
        self.assertEqual(Delivery(h.store,h.root).current(h.job)['stage'],'sending')
        self.assertEqual(h.state['phase'],'uncertain')
        self.assertEqual(h.edit.Value,'Human edit after validation')

    def test_late_wake_does_not_send_a_burst_for_missed_slots(self):
        h=self.make()
        for at in (5000,5001,5002,5003):h.cycle(at)
        self.assertEqual(h.clicks,1);self.assertEqual(h.state['due'],5061)

    def test_disabling_job_after_send_claim_cancels_before_any_click(self):
        h=self.make();h.cycle(61)
        original=Delivery.begin_send
        def claim(delivery,job):
            result=original(delivery,job);h.job['enabled']=False;return result
        with patch.object(Delivery,'begin_send',claim):h.cycle(62)
        self.assertEqual(h.clicks,0);self.assertIsNone(h.state['last_sent'])
        self.assertEqual(Delivery(h.store,h.root).current(h.job)['stage'],'prepared')

    def test_read_failure_after_claim_can_retry_because_invoke_was_not_called(self):
        h=self.make();h.cycle(61)
        original=Delivery.begin_send
        def claim(delivery,job):
            result=original(delivery,job);h.windows=[];return result
        with patch.object(Delivery,'begin_send',claim):self.assertEqual(h.cycle(62)['action'],'error')
        self.assertEqual(h.clicks,0);self.assertEqual(h.state['phase'],'drafted')
        h.windows=[h.controls];h.cycle(68);h.cycle(69)
        self.assertEqual(h.clicks,1);self.assertEqual(h.state['last_sent'],68)


if __name__=='__main__':unittest.main()
