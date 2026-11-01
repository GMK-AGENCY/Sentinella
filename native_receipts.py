"""Exact native evidence. Assistant output, absence and text similarity prove nothing."""
from datetime import datetime
import json
from pathlib import Path
import time
import uuid

from delivery_journal import digest


def codex_page_receipt(attempt, page, now):
    binding=json.loads(attempt['binding_json'])
    found={}
    for entry in page.get('data',[]):
        item=entry.get('item',{})
        if item.get('type')!='userMessage' or item.get('clientId')!=attempt['id']:
            continue
        content=item.get('content',[])
        if not content or any(part.get('type')!='text' for part in content):
            raise ValueError('Contenuto della ricevuta modificato dal destinatario')
        text=''.join(part.get('text','') for part in content)
        stamp=(entry.get('startedAtMs') or item.get('startedAtMs') or 0)/1000
        if digest(text)!=attempt['message_hash'] or not attempt['created']-1<=stamp<=now+1:
            raise ValueError('Ricevuta Codex non pertinente per testo o data')
        physical=str(uuid.UUID(item['id']))
        found[physical]={'app':'codex','target_id':binding['runtime_id'],
            'correlation':attempt['id'],'physical_id':physical,'message_hash':digest(text),
            'received_at':stamp,'native_item':entry}
    if len(found)>1:raise ValueError('Ricevute multiple per lo stesso invio: nessun nuovo invio')
    return next(iter(found.values()),None)


def claude_receipt(attempt, now=None, max_bytes=8*1024*1024):
    now=time.time() if now is None else now
    binding=json.loads(attempt['binding_json'])
    path=Path(binding['history'])
    cursor=int(binding['history_offset'])
    if not path.is_file() or path.stat().st_size<cursor:
        raise ValueError('Cronologia del destinatario non disponibile: esito non determinabile')
    matches={}
    read=0
    deadline=time.monotonic()+1.5
    with path.open('rb') as stream:
        stream.seek(cursor)
        while read<max_bytes and time.monotonic()<deadline:
            position=stream.tell()
            line=stream.readline(2*1024*1024)
            if not line:break
            if not line.endswith(b'\n'):
                if len(line)>=2*1024*1024:
                    raise ValueError('Record della cronologia troppo grande: verifica richiesta')
                break  # Never consume an incomplete record.
            read+=len(line);cursor=stream.tell()
            try:
                record=json.loads(line)
                origin=record.get('origin',{})
                stamp=datetime.fromisoformat(record.get('timestamp','').replace('Z','+00:00')).timestamp()
            except (ValueError,TypeError):continue
            if (record.get('type')!='user' or record.get('message',{}).get('role')!='user'
                    or record.get('isSidechain') or record.get('sessionId')!=binding['runtime_id']
                    or record.get('entrypoint')!='claude-desktop' or record.get('turnOrigin')!='peer'
                    or origin.get('kind')!='peer' or origin.get('plugin')!='sentinella-bridge'
                    or origin.get('fromMode')!='prompting'
                    or origin.get('name')!=binding['sender_name']):
                continue
            body=origin.get('body')
            if not isinstance(body,str) or digest(body)!=attempt['message_hash'] or not attempt['created']-1<=stamp<=now+1:
                raise ValueError('Il destinatario ha registrato un testo o una data diversi')
            physical=str(uuid.UUID(record['uuid']))
            str(uuid.UUID(origin['msg_id']))
            matches[physical]={'app':'claude','target_id':binding['runtime_id'],
                'correlation':attempt['id'],'physical_id':physical,'message_hash':digest(body),
                'received_at':stamp,'source':str(path),'byte_offset':position,'native_record':record}
    if len(matches)>1:raise ValueError('Ricevute multiple per lo stesso invio: verifica richiesta')
    return next(iter(matches.values()),None),cursor
