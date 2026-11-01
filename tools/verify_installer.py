"""Real install/uninstall verification, confined to an isolated Windows runner."""
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from bounded_process import run_bounded
from config_store import default_config
from app_version import VERSION


def checked(command,cwd,timeout=20):
    result=run_bounded(command,cwd=cwd,timeout=timeout)
    if result['timed_out'] or result['returncode']:
        raise RuntimeError('Installer verification failed: '+json.dumps(result))
    return result


def main():
    if sys.platform!='win32' or os.environ.get('GITHUB_ACTIONS')!='true':
        raise SystemExit('Installation tests run only on the isolated hosted Windows runner')
    installer=ROOT/f'dist/Sentinella-{VERSION}-Windows-x64-Setup.exe'
    if not installer.is_file():raise FileNotFoundError(installer)
    build=ROOT/'build';build.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='installer-check-',dir=build) as folder:
        profile=Path(folder).resolve()
        if not profile.is_relative_to(build.resolve()):raise ValueError('Installation test escaped its workspace')
        install=profile/'app';data=profile/'data';data.mkdir()
        originals={'approval-config.json':json.dumps(default_config()).encode(),
                   'messages-config.json':b'{"jobs":[]}',
                   'keep-private-data.txt':b'Preserve user data during install and uninstall.'}
        for name,body in originals.items():(data/name).write_bytes(body)
        previous=os.environ.get('SENTINELLA_DATA_DIR');os.environ['SENTINELLA_DATA_DIR']=str(data)
        try:
            checked([str(installer),'/VERYSILENT','/SUPPRESSMSGBOXES','/SP-','/NORESTART',
                     '/NOICONS','/TASKS=','/DIR='+str(install),'/LOG='+str(profile/'install.log')],ROOT,25)
            exe=install/'Sentinella.exe'
            diagnosis=json.loads(checked([str(exe),'--diagnose'],install,20)['stdout'].strip().splitlines()[-1])
            if not diagnosis.get('frozen') or not diagnosis.get('dependencies_loaded'):
                raise RuntimeError('Installed application is missing its embedded runtime')
            worker=json.loads(checked([str(exe),'--process','adapter_entry','native_delivery_worker.py'],install)['stdout'].strip().splitlines()[-1])
            if worker.get('action')!='idle':raise RuntimeError('Installed native worker failed')
            bridge=install/'_internal/assets/native_claude_bridge'
            for name in ('.claude-plugin/plugin.json','hooks/hooks.json','hooks/register.js'):
                if not (bridge/name).is_file():raise RuntimeError('Installed Claude bridge is missing')
            checked([str(install/'unins000.exe'),'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART'],ROOT,20)
            if exe.exists():raise RuntimeError('Uninstall did not remove the owned application')
            for name,body in originals.items():
                if (data/name).read_bytes()!=body:raise RuntimeError('Install/uninstall altered private data: '+name)
            report={'installed':True,'frozen_runtime':True,'native_worker':worker['action'],
                    'bridge_assets':True,'uninstalled':True,'private_data_preserved':True,'gui_started':False}
            (build/'installer-verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
            print(json.dumps(report),flush=True)
        finally:
            if previous is None:os.environ.pop('SENTINELLA_DATA_DIR',None)
            else:os.environ['SENTINELLA_DATA_DIR']=previous


if __name__=='__main__':main()
