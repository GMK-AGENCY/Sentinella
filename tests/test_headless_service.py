import builtins
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
import uuid

from headless_service import run


class HeadlessServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.logs=self.root/'logs';self.logs.mkdir()
        self.run_id=str(uuid.uuid4());self.wall=1000.;self.mono=0.;self.sleeps=0
        self.child=Mock();self.child.poll.return_value=None

    def tearDown(self):self.tmp.cleanup()

    def write(self,name,value):
        (self.logs/name).write_text(json.dumps(value),encoding='utf-8')

    def show(self,**changes):
        self.write('show-window.json',{'run_id':self.run_id,'requested_at':self.wall,**changes})

    def close(self):self.write('exit-intent.json',{'run_id':self.run_id})

    def execute(self,step,children=None,advance=.5):
        def sleep(seconds):
            self.assertLessEqual(seconds,60)
            self.sleeps+=1;self.wall+=advance;self.mono+=advance
            if self.sleeps>30:raise AssertionError('Watchdog test did not return')
            step()
        with patch.dict(os.environ,{'SENTINELLA_RUN_ID':self.run_id}), \
             patch('headless_service.time.time',side_effect=lambda:self.wall), \
             patch('headless_service.time.monotonic',side_effect=lambda:self.mono), \
             patch('headless_service.time.sleep',side_effect=sleep), \
             patch('headless_service.subprocess.Popen',side_effect=children or [self.child]) as popen, \
             patch('headless_service.stop_tree') as stopped:
            result=run(self.root)
        return result,popen,stopped

    def test_no_tk_or_input_and_matching_request_adopts_same_supervisor(self):
        original=builtins.__import__
        def guarded(name,*args,**kwargs):
            if name=='tkinter' or name.startswith('tkinter.'):
                raise AssertionError('Headless mode must not load Tk')
            return original(name,*args,**kwargs)
        with patch('builtins.__import__',side_effect=guarded):
            result,popen,stopped=self.execute(self.show)
        self.assertEqual((True,self.child),result);self.assertEqual(1,popen.call_count)
        stopped.assert_not_called()
        self.assertEqual(self.run_id,popen.call_args.kwargs['env']['SENTINELLA_RUN_ID'])
        heartbeat=json.loads((self.logs/'gui-heartbeat.json').read_text())
        self.assertEqual('headless',heartbeat['controller_kind']);self.assertEqual('absent',heartbeat['window_state'])

    def test_wrong_run_or_expired_show_is_consumed_without_adoption(self):
        def step():
            if self.sleeps==1:self.show(run_id='old-run')
            elif self.sleeps==2:
                self.assertFalse((self.logs/'show-window.json').exists())
                self.show(requested_at=self.wall-61)
            else:
                self.assertFalse((self.logs/'show-window.json').exists());self.close()
        result,popen,stopped=self.execute(step)
        self.assertEqual((False,None),result);self.assertEqual(1,popen.call_count)
        stopped.assert_called_once_with(self.child)

    def test_intentional_close_never_starts_or_restarts_services(self):
        self.close()
        result,popen,stopped=self.execute(lambda:None)
        self.assertEqual((False,None),result);popen.assert_not_called();stopped.assert_not_called()

    def test_crashed_supervisor_restarts_with_bounded_backoff(self):
        self.child.poll.return_value=9
        replacement=Mock();replacement.poll.return_value=None
        def step():
            if self.sleeps==4:self.show()
        result,popen,stopped=self.execute(step,[self.child,replacement],advance=3)
        self.assertEqual((True,replacement),result);self.assertEqual(2,popen.call_count)
        stopped.assert_called_once_with(self.child)

    def test_live_but_stale_supervisor_is_stopped_and_replaced(self):
        replacement=Mock();replacement.poll.return_value=None
        def step():
            if self.sleeps==3:self.show()
        result,popen,stopped=self.execute(step,[self.child,replacement],advance=46)
        self.assertEqual((True,replacement),result);self.assertEqual(2,popen.call_count)
        stopped.assert_called_once_with(self.child)

    def test_current_functional_heartbeat_prevents_false_restart(self):
        def step():
            self.write('supervisor-health.json',{'updated_at':datetime.fromtimestamp(self.wall,timezone.utc).isoformat()})
            if self.sleeps==5:self.show()
        result,popen,stopped=self.execute(step,advance=30)
        self.assertEqual((True,self.child),result);self.assertEqual(1,popen.call_count);stopped.assert_not_called()

    def test_start_failure_recovers_without_crashing_controller(self):
        def step():
            if self.sleeps==3:self.show()
        result,popen,stopped=self.execute(step,[OSError('temporarily unavailable'),self.child],advance=3)
        self.assertEqual((True,self.child),result);self.assertEqual(2,popen.call_count);stopped.assert_not_called()

    def test_predecessor_heartbeat_cannot_hide_a_hung_new_supervisor(self):
        self.write('supervisor-health.json',{'updated_at':datetime.fromtimestamp(999,timezone.utc).isoformat()})
        replacement=Mock();replacement.poll.return_value=None
        def step():
            if self.sleeps==3:self.show()
        result,popen,stopped=self.execute(step,[self.child,replacement],advance=46)
        self.assertEqual((True,replacement),result);self.assertEqual(2,popen.call_count)
        stopped.assert_called_once_with(self.child)


if __name__=='__main__':unittest.main()
