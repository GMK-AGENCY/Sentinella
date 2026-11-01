"""Read the qualified desktop owner's published activity, by full native ID.

No names, short peer references, credentials, sockets or chat bodies are used.
This is an observed state, not an atomic reservation of the receiver's turn.
"""
import json
import math
from pathlib import Path
import time

import psutil
from native_chat_state import ChatState


def claude_chat_state(owner, *, runtime_id, logical_id, session_dir=None, now=None):
    now=time.time() if now is None else now
    unavailable=lambda reason:ChatState('unknown',reason)
    try:
        created=owner.create_time()
        if not owner.is_running():return unavailable('Il processo destinatario non è più attivo')
        args=owner.cmdline()
        bound=('--resume='+runtime_id in args or any(arg=='--resume' and args[i+1]==runtime_id for i,arg in enumerate(args[:-1])))
        if not bound:return unavailable('Il processo non corrisponde all’ID destinatario')
        path=Path(session_dir or Path.home()/'.claude/sessions')/(str(owner.pid)+'.json')
        before=path.stat()
        if not 0<before.st_size<=32768:return unavailable('Formato dello stato Claude non qualificato')
        data=json.loads(path.read_bytes())
        after=path.stat()
        if (before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_ino,after.st_size,after.st_mtime_ns):
            return ChatState('unknown','Lo stato Claude è cambiato durante la lettura',code='updating')
        if (data.get('version')!='2.1.308' or data.get('pid')!=owner.pid
                or data.get('sessionId')!=runtime_id or data.get('hostSessionId')!='local_'+logical_id
                or data.get('entrypoint')!='claude-desktop' or data.get('kind')!='interactive'):
            return unavailable('Identità o versione dello stato Claude non qualificata')
        token=data.get('procStart')
        if not isinstance(token,str) or not token.isdecimal():return unavailable('Identità del processo Claude incompleta')
        native_created=int(token)/10000000-11644473600
        updated=data.get('updatedAt');status_at=data.get('statusUpdatedAt')
        if (abs(native_created-created)>.001 or not owner.is_running() or owner.create_time()!=created
                or any(type(value) not in (int,float) or not math.isfinite(value) for value in (updated,status_at))
                or not created*1000-1000<=status_at<=updated<=(now+5)*1000):
            return unavailable('Stato Claude non coerente con il processo destinatario')
        state=data.get('status')
        if state=='idle':return ChatState('idle','Il proprietario desktop dichiara la chat libera')
        if state=='busy':return ChatState('active','La chat sta elaborando un turno')
        return unavailable('Stato Claude non riconosciuto; attendo una lettura verificabile')
    except (OSError,ValueError,TypeError,OverflowError,psutil.Error):
        return unavailable('Stato Claude non disponibile; nessuna deduzione di chat libera')
