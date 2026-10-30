"""Fault recovery uses real SQLite state; an error is never a receipt."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

from message_engine import TimerStore,run_cycle
from schedule_display import schedule_display


class DeliveryFailureTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=TimerStore(Path(self.temp.name)/'messages.sqlite3');self.addCleanup(self.store.db.close)
        self.job=dict(id='job',version='v1',enabled=True,app='codex',title='Test',group='Recenti',
                      message='Exact message',interval_seconds=60,activated_at=0,send_when_present=False)
        self.jobs=[self.job]
        self.store.prepare(self.job,0)

    def cycle(self,at,adapter,config=None):
        with patch('message_engine.load_jobs',return_value=self.jobs):
            return run_cycle(config or {},self.store,now=at,adapter=adapter)

    def test_window_error_keeps_due_phase_and_last_sent_and_is_persisted(self):
        result=self.cycle(61,Mock(side_effect=RuntimeError('Finestra non univoca')))
        self.assertEqual(result['action'],'error')
        row=self.store.rows()[0]
        self.assertEqual((row['due'],row['phase'],row['last_sent']),(60,'idle',None))
        self.assertIn('Finestra non univoca',row['delivery_error'])
        self.assertEqual(row['error_since'],61)
        self.assertEqual(self.store.history(outcome='attention')[0]['event'],'Errore invio')

    def test_pc_in_use_does_not_hide_a_persisted_delivery_failure(self):
        self.cycle(61,Mock(side_effect=RuntimeError('Tre finestre')))
        self.store.update(self.job,'idle','PC in uso')
        state=self.store.rows()[0]
        shown=schedule_display(self.job,state,{},70,'PC in uso: attendo 300 secondi')
        self.assertEqual(shown['status'],'Invio bloccato')
        self.assertEqual(shown['severity'],'warning')
        self.assertIn('Tre finestre',shown['detail'])

    def test_restart_keeps_failure_and_does_not_reset_deadline(self):
        self.cycle(61,Mock(side_effect=OSError('Provider non disponibile')))
        other=TimerStore(Path(self.temp.name)/'messages.sqlite3')
        try:self.assertEqual(other.rows(),self.store.rows())
        finally:other.db.close()

    def test_backoff_skips_calls_and_does_not_flood_history(self):
        adapter=Mock(side_effect=RuntimeError('Errore invariato'))
        self.cycle(61,adapter)
        for at in (62,63,64,65):self.cycle(at,adapter)
        self.assertEqual(adapter.call_count,1)
        self.cycle(66,adapter)
        self.assertEqual(adapter.call_count,2)
        failures=[r for r in self.store.history() if r['event']=='Errore invio']
        self.assertEqual(len(failures),1)
        self.assertEqual(self.store.rows()[0]['error_attempts'],2)

    def test_skipped_or_paused_cycle_does_not_report_recovery(self):
        self.cycle(61,Mock(side_effect=RuntimeError('Fail')))
        self.cycle(62,Mock(side_effect=AssertionError('Retry not due')))
        self.cycle(70,Mock(side_effect=AssertionError('Paused')),{'paused':True})
        self.assertIn('delivery_error',self.store.rows()[0])

    def test_fresh_successful_observation_resolves_error_without_claiming_delivery(self):
        self.cycle(61,Mock(side_effect=RuntimeError('Fail')))
        self.cycle(66,lambda *_:{'action':'none','reason':'Bozza presente'})
        row=self.store.rows()[0]
        self.assertNotIn('delivery_error',row)
        self.assertIsNone(row['last_sent'])
        self.assertIn('Bozza presente',row['detail'])

    def test_exception_after_invoke_keeps_uncertain_and_never_invokes_twice(self):
        button=Mock()
        button.GetInvokePattern.return_value.Invoke.side_effect=RuntimeError('Disconnected after Invoke')
        self.store.update(self.job,'drafted','Owned preparation')
        adapter=Mock(return_value={'action':'ready','button':button})
        self.cycle(61,adapter)
        for at in (62,70,1000):self.cycle(at,adapter)
        self.assertEqual(self.store.rows()[0]['phase'],'uncertain')
        self.assertIsNone(self.store.rows()[0]['last_sent'])
        self.assertEqual(button.GetInvokePattern.return_value.Invoke.call_count,1)
        self.assertEqual(adapter.call_count,1)

    def test_one_broken_job_does_not_starve_another(self):
        other={**self.job,'id':'other','title':'Other'}
        self.jobs.append(other);self.store.prepare(other,0)
        def adapter(_,job,__):
            if job['id']=='job':raise RuntimeError('First app unavailable')
            return {'action':'drafted'}
        self.cycle(61,adapter)
        result=self.cycle(62,adapter)
        self.assertEqual((result['job'],result['action']),('other','drafted'))
        self.assertIn('delivery_error',self.store.rows()[0])

    def test_config_version_change_does_not_attach_old_error(self):
        self.cycle(61,Mock(side_effect=RuntimeError('Old failure')))
        self.job['version']='v2';self.job['activated_at']=70
        self.store.prepare(self.job,70)
        self.assertNotIn('delivery_error',self.store.rows()[0])


if __name__=='__main__':unittest.main()
