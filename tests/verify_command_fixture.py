"""Opt-in Windows check: clicks only our own harmless marker fixture.

No assistant chat is selected and no shell command is run by the fixture.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bounded_process import run_bounded, stop_owned
from config_store import default_config


def run():
    with tempfile.TemporaryDirectory(prefix='sentinella-command-test-') as folder:
        profile = Path(folder)
        (profile/'logs').mkdir()
        (profile/'test-project').mkdir()
        config = default_config()
        config.update(paused=False,idle_seconds_before_actions=0)
        (profile/'approval-config.json').write_text(json.dumps(config),encoding='utf-8')
        env={**os.environ,'SENTINELLA_DATA_DIR':folder}
        process=subprocess.Popen([sys.executable,str(ROOT/'approval_simulation.py'),'--command'],env=env,
                                 stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            ready=profile/'logs/approval-simulation-config.json'
            until=time.monotonic()+5
            while not ready.exists() and time.monotonic()<until:time.sleep(.05)
            active=json.loads(ready.read_text(encoding='utf-8'))
            # The live gate reads the isolated profile too. Production config is untouched.
            (profile/'approval-config.json').write_text(json.dumps(active),encoding='utf-8')
            code='''import json,os,sys
os.environ['SENTINELLA_DATA_DIR']=sys.argv[1]
from approval_desktop import scan
config=json.load(open(sys.argv[1]+'/approval-config.json',encoding='utf-8'))
print(json.dumps(scan(config,'codex'),ensure_ascii=True))'''
            result=run_bounded([sys.executable,'-c',code,folder],timeout=15,cwd=ROOT)
            assert result['returncode']==0 and not result['timed_out'],result
            observation=json.loads(result['stdout'])
            assert observation['invoked'],observation
            assert observation['button']=='Consenti una volta',observation
            outcome=json.loads((profile/'logs/simulazione-comando-risultato.json').read_text(encoding='utf-8'))
            marker=Path(outcome['marker'])
            assert outcome['continued'] and marker.read_text(encoding='utf-8')=='SIMULAZIONE_CODEX_RIPARTITA: comando\n'
            approvals=[json.loads(line) for line in (profile/'logs/consensi.jsonl').read_text(encoding='utf-8').splitlines()]
            assert sum(event['status']=='invoked' for event in approvals)==1
            print(json.dumps({'native_fixture':True,'clicked':'Consenti una volta','marker_written':True,
                              'invocations':1,'production_config_changed':False,'elapsed_seconds':result['elapsed_seconds']}))
        finally:stop_owned(process,{})


if __name__=='__main__':run()
