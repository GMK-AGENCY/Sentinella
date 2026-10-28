"""Opt-in Windows integration check; never clicks consent or sends a message."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bounded_process import run_bounded, stop_owned


def run():
    with tempfile.TemporaryDirectory() as folder:
        ready = Path(folder) / "ready.json"
        process = subprocess.Popen([sys.executable, str(ROOT / "tests/native_controls_fixture.py"), str(ready)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            deadline = time.monotonic() + 4
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(.05)
            handles = json.loads(ready.read_text())
            code = '''import json,psutil,sys,time
import uiautomation as auto
from question_desktop import native_controls,question_card
from approval_desktop import scan
handles=json.loads(sys.argv[1]);root=sys.argv[2]
q=auto.ControlFromHandle(handles['question'])
controls=native_controls(q);card=question_card(controls)
c=auto.ControlFromHandle(handles['consent'])
config={'enabled':True,'paused':False,'idle_seconds_before_actions':30,
        'allowed_project_roots':[root+'/test-project'], 'simulation_window_handle':handles['consent'],
        'apps':{'codex':{'enabled':True,'approvals_enabled':True,'process_path':psutil.Process(c.ProcessId).exe()}}}
result=scan(config,'codex',inspect_only=True)
print(json.dumps({'native_question_detected':bool(card),'question_text':card['text'] if card else None,
                 'question_controls': [{'type':c.ControlTypeName,'name':c.Name} for c in controls],
                 'native_consent_detected':any(d.get('eligible') for d in result['dialogs']),
                 'consent_invoked':result['invoked'],'dialogs':result['dialogs']},ensure_ascii=True))'''
            result = run_bounded([sys.executable, "-c", code, json.dumps(handles), str(ROOT)], timeout=10, cwd=ROOT)
            if result["timed_out"] or result["returncode"]:
                raise RuntimeError(json.dumps(result))
            observation = json.loads(result["stdout"])
            assert observation["native_question_detected"], observation
            assert observation["native_consent_detected"], observation
            assert not observation["consent_invoked"], observation
            assert not ready.with_suffix('.unexpected-click').exists()
            observation['no_input_or_file_operation'] = True
            observation['elapsed_seconds'] = result['elapsed_seconds']
            print(json.dumps(observation, ensure_ascii=False))
        finally:
            stop_owned(process, {})


if __name__ == "__main__":
    run()
