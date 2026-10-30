"""One stalled receipt or a presence restriction must not stop other timers."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from message_engine import TimerStore,run_cycle


class SchedulerFairnessTests(unittest.TestCase):
    def test_receipt_wait_presence_and_failed_history_leave_other_jobs_runnable(self):
        for blockage in ('receipt','presence','history'):
            with self.subTest(blockage=blockage),tempfile.TemporaryDirectory() as folder:
                store=TimerStore(Path(folder)/'timers.sqlite3')
                try:
                    first=dict(id='first',version='v1',app='claude',session_id='first-session',title='First',group='Test',
                               message='One',enabled=True,activated_at=0,interval_seconds=60,send_when_present=False)
                    second={**first,'id':'second','session_id':'second-session','title':'Second','message':'Two','send_when_present':True}
                    for job in (first,second):store.prepare(job,0)
                    if blockage in ('receipt','history'):store.update(first,'uncertain','Awaiting genuine receipt')
                    def reconcile(job,_):
                        if job['id']!='first':return None
                        if blockage=='history':raise OSError('History temporarily unavailable')
                        if blockage=='receipt':return {'action':'awaiting_receipt'}
                    bridge=SimpleNamespace(reconcile=reconcile,cleanup_needed=lambda _:False,current=lambda _:None)
                    config=dict(paused=False,idle_seconds_before_actions=300,apps={'claude':{'process_path':'fixture.exe'}})
                    with patch('message_engine.load_jobs',side_effect=lambda:[dict(first),dict(second)]), \
                         patch('claude_delivery.Delivery',return_value=bridge), \
                         patch('automation_policy.idle_seconds',return_value=0 if blockage=='presence' else 1000), \
                         patch('chat_desktop.step',return_value={'action':'drafted'}) as adapter:
                        result=run_cycle(config,store,now=61)
                    self.assertEqual((result['job'],result['action']),('second','drafted'))
                    self.assertEqual(adapter.call_args.args[1]['id'],'second')
                    self.assertIsNone(store.rows()[0]['last_sent'])
                finally:store.db.close()

    def test_history_error_is_recorded_once_for_the_correct_job(self):
        with tempfile.TemporaryDirectory() as folder:
            store=TimerStore(Path(folder)/'timers.sqlite3')
            try:
                first=dict(id='first',version='v1',app='claude',session_id='first-session',title='First',group='Test',
                           message='One',enabled=True,activated_at=0,interval_seconds=60)
                second={**first,'id':'second','session_id':'second-session'}
                for job in (first,second):store.prepare(job,0)
                bridge=SimpleNamespace(reconcile=lambda job,_: (_ for _ in ()).throw(OSError('Unreadable')) if job['id']=='first' else None,
                                       cleanup_needed=lambda _:False)
                with patch('message_engine.load_jobs',return_value=[first,second]),patch('claude_delivery.Delivery',return_value=bridge):
                    result=run_cycle({},store,now=1,reconcile_only=True)
                self.assertEqual(result['job'],'first')
                rows={r['id']:r for r in store.rows()}
                self.assertEqual(rows['first']['error_attempts'],1)
                self.assertFalse(rows['second'].get('delivery_error'))
            finally:store.db.close()


if __name__=='__main__':unittest.main()
