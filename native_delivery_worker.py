"""Bounded child process of Sentinella; independent of Codex chats/automations."""
import json
from native_delivery import run
from runtime_paths import data_root

if __name__=='__main__':
    try:print(json.dumps(run(data_root()),ensure_ascii=True),flush=True)
    except Exception as exc:
        print(json.dumps({'action':'error','error':f'{type(exc).__name__}: {exc}'}),flush=True)
        raise SystemExit(2)
