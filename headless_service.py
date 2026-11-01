"""Windowless application controller; the normal supervisor owns deliveries.

No Tk root, window, keyboard/mouse hook or notification reader is constructed.
An explicit normal launch requests the existing GUI and adopts the SAME
supervisor, without restarting an in-flight sender or resetting its timers.
"""
import os
import json
import math
from pathlib import Path
import subprocess
import time
import uuid

from runtime_paths import command
from sentinel_launcher import atomic_json,read_json,stop_tree
from service_recovery import ServiceRecovery,supervisor_stale


def _event(logs, event, **values):
    try:
        with (logs/'headless-events.jsonl').open('a',encoding='utf-8') as stream:
            stream.write(json.dumps({'at':time.time(),'event':event,**values},ensure_ascii=False)+'\n')
    except OSError:
        pass  # A diagnostic failure must not stop the actual watchdog.


def _show_requested(path, run_id, now):
    if not path.exists():return False
    value=read_json(path)
    try:path.unlink()
    except OSError:return False
    stamp=value.get('requested_at') if isinstance(value,dict) else None
    return (isinstance(value,dict) and value.get('run_id')==run_id
            and isinstance(stamp,(int,float)) and math.isfinite(stamp)
            and 0<=now-stamp<=60)


def run(root):
    root=Path(root);logs=root/'logs';logs.mkdir(exist_ok=True)
    run_id=os.environ.setdefault('SENTINELLA_RUN_ID',str(uuid.uuid4()))
    supervisor=None;next_start=0.;started_wall=0.;adopted=False
    recovery=ServiceRecovery()
    output=(logs/'headless-supervisor-output.log').open('ab')
    try:
        while True:
            now=time.time()
            try:
                atomic_json(logs/'gui-heartbeat.json',{'run_id':run_id,'pid':os.getpid(),
                    'updated_at':now,'poll_updated_at':now,'controller_kind':'headless','window_state':'absent'})
            except OSError as exc:
                _event(logs,'controller_heartbeat_error',error=str(exc))
            if read_json(logs/'exit-intent.json').get('run_id')==run_id:return False,None
            if supervisor is not None:
                health=read_json(logs/'supervisor-health.json')
                reason=('Processo interrotto' if supervisor.poll() is not None else
                        'Nessun aggiornamento per 90 secondi' if supervisor_stale(health,started_wall,now) else None)
                if reason:
                    stop_tree(supervisor)
                    supervisor=None
                    delay=recovery.failed(time.monotonic());next_start=time.monotonic()+delay
                    _event(logs,'supervisor_recovery',reason=reason,retry_seconds=delay,run_id=run_id)
                elif health:
                    recovery.healthy(time.monotonic())
            if supervisor is None and time.monotonic()>=next_start:
                started_wall=now
                recovery.started()
                try:
                    supervisor=subprocess.Popen(command('question_supervisor'),cwd=root,
                        env={**os.environ,'SENTINELLA_RUN_ID':run_id},stdin=subprocess.DEVNULL,
                        stdout=output,stderr=output,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                except OSError as exc:
                    delay=recovery.failed(time.monotonic());next_start=time.monotonic()+delay
                    _event(logs,'supervisor_start_error',error=str(exc),retry_seconds=delay,run_id=run_id)
            request=logs/'show-window.json'
            if _show_requested(request,run_id,now):
                adopted=True
                return True,supervisor
            time.sleep(.5)
    finally:
        if supervisor is not None and not adopted:stop_tree(supervisor)
        output.close()
