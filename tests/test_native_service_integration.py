"""Real GUI -> supervisor -> native worker, only on an isolated hosted runner.

The recipient executable deliberately does not exist: reaching that rejection
proves authority and routing, with no opportunity to send to any real chat.
"""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

import psutil
from bounded_process import stop_owned
from config_store import default_config
from runtime_paths import SOURCE_ROOT


@unittest.skipUnless(sys.platform=='win32' and os.environ.get('GITHUB_ACTIONS')=='true',
                     'GUI integration runs only on the hosted Windows runner')
class NativeServiceIntegrationTests(unittest.TestCase):
    def test_gui_authorizes_native_worker_without_an_available_recipient(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryFile() as output:
            root=Path(folder);(root/'logs').mkdir()
            config=default_config();config.update(paused=False,idle_seconds_before_actions=0)
            config['apps']['codex']['process_path']='C:/SentinellaFixture/MissingCodex.exe'
            (root/'approval-config.json').write_text(json.dumps(config),encoding='utf-8')
            activated=time.time()-61
            job={'id':str(uuid.uuid4()),'version':str(uuid.uuid4()),'app':'codex',
                'thread_id':str(uuid.uuid4()),'title':'Missing test recipient','group':'Test',
                'message':'This fixture must never send a message.','enabled':True,'repeat':True,
                'interval_seconds':60,'activated_at':activated,'send_when_active':True,'send_when_present':True}
            (root/'messages-config.json').write_text(json.dumps({'jobs':[job]}),encoding='utf-8')
            run_id=str(uuid.uuid4())
            proc=subprocess.Popen([sys.executable,str(SOURCE_ROOT/'notification_watch.py')],cwd=root,
                env={**os.environ,'SENTINELLA_DATA_DIR':str(root),'SENTINELLA_RUN_ID':run_id},
                stdin=subprocess.DEVNULL,stdout=output,stderr=output,
                creationflags=subprocess.CREATE_NO_WINDOW)
            known={proc.pid:psutil.Process(proc.pid)};known[proc.pid].create_time()
            failure=None;deadline=time.monotonic()+25
            try:
                while proc.poll() is None and time.monotonic()<deadline:
                    for child in known[proc.pid].children(recursive=True):known[child.pid]=child
                    try:
                        db=sqlite3.connect((root/'logs/messages.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=.1)
                        try:
                            failure=db.execute('SELECT error FROM message_failures WHERE job_id=?',(job['id'],)).fetchone()
                            if failure:
                                self.assertIn('Applicazione chiusa',failure[0])
                                self.assertEqual(0,db.execute('SELECT count(*) FROM native_attempts').fetchone()[0])
                                self.assertAlmostEqual(activated+60,db.execute('SELECT due FROM timers WHERE id=?',(job['id'],)).fetchone()[0])
                        finally:db.close()
                    except sqlite3.Error:pass
                    if failure:break
                    time.sleep(.1)
                if not failure:
                    output.seek(0);details=output.read(3000).decode('utf-8',errors='replace')
                    self.fail('Native worker did not reach recipient discovery: '+details)
                heartbeat=json.loads((root/'logs/gui-heartbeat.json').read_text(encoding='utf-8'))
                self.assertEqual(run_id,heartbeat['run_id'])
                self.assertEqual(proc.pid,heartbeat['pid'])
            finally:stop_owned(proc,known)


if __name__=='__main__':unittest.main()
