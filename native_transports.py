"""Native local delivery, never window navigation or a composer fallback.

An acknowledged queue/peer call is NOT a receipt. Capability failures remain
explicit. Only the separately reconciled native user record proves delivery.
"""
import json
from pathlib import Path
import shutil
import time
import uuid

import psutil

from native_process import OwnedJsonProcess, ProtocolError
from native_receipts import codex_page_receipt, claude_receipt
from runtime_paths import SOURCE_ROOT


class NotDispatched(ProtocolError):
    """Raised only before the transport's first send/queue effect."""


def receiver_processes(app, config):
    from desktop_identity import executable_matches
    configured=config.get('apps',{}).get(app,{}).get('process_path')
    if not configured: raise ProtocolError('Applicazione non configurata: esegui Rileva applicazioni')
    found=[]
    # Do not read every process's command line just to find an application.
    for proc in psutil.process_iter(['exe']):
        try:
            if proc.info['exe'] and executable_matches(proc.info['exe'],configured): found.append(proc)
        except (psutil.Error,OSError): continue
    if not found: raise ProtocolError('Applicazione chiusa: nessun invio, nessuna riapertura automatica')
    return found


class CodexTransport:
    def __init__(self, job, config, root, readonly=False, binding=None):
        self.client=None
        self.job=job
        # Prefer the CLI shipped with the running app over an unrelated PATH version.
        roots=[] if readonly else sorted({Path(p.info['exe']).parent for p in receiver_processes('codex',config)})
        candidates=[p for root in roots for p in (root/'resources/codex.exe',root/'resources/bin/codex.exe') if p.is_file()]
        executable=(binding or {}).get('cli_executable') if readonly else (str(candidates[0]) if candidates else shutil.which('codex'))
        if not executable: raise ProtocolError('CLI Codex compatibile non disponibile: aggiorna Codex')
        if not Path(executable).is_file():raise ProtocolError('CLI Codex della prenotazione non disponibile; nessun reinvio')
        if not readonly and not job.get('send_when_active'):
            raise ProtocolError('Stato della chat Codex non verificabile in background con questa versione; invio a chat libera non disponibile')
        try:
            self.client=OwnedJsonProcess([executable,'app-server','--listen','stdio://'],root)
            initialized=self.client.rpc('initialize',{'clientInfo':{'name':'sentinella_native','version':'1.0.0'},
                'capabilities':{'experimentalApi':True}})
            self.client.send({'method':'initialized','params':{}})
            self.unloaded()
            thread_id=str(uuid.UUID(job['thread_id']))
            # The read-only index excludes archived sessions, by ID, not title.
            from chat_catalog import codex_metadata
            if not readonly and len(codex_metadata(set(),thread_ids=(thread_id,)))!=1:
                raise ProtocolError('ID Codex assente o archiviato: nessun invio')
            thread=self.client.rpc('thread/read',{'threadId':thread_id,'includeTurns':False})['thread']
            if thread.get('id')!=thread_id: raise ProtocolError('Identità Codex non verificata')
            if not readonly:
                queue=self.client.rpc('thread/queue/list',{'threadId':thread_id,'limit':1})
                if not isinstance(queue.get('data'),list):raise ProtocolError('Coda Codex non compatibile: aggiorna il collegamento')
            self.binding={'app':'codex','logical_id':thread_id,'runtime_id':thread_id,'revision':1,
                'cli_executable':executable,'server_version':initialized.get('userAgent')}
        except BaseException:
            self.close();raise

    def unloaded(self):
        if self.client.rpc('thread/loaded/list',{}).get('data')!=[]:
            raise ProtocolError('Il mittente non deve possedere sessioni: invio bloccato')

    def prepare(self, attempt): pass

    def dispatch(self, attempt):
        self.unloaded()
        if not self.guard():raise NotDispatched('Pausa, chiusura o modifica prima dell’invio')
        reply=self.client.rpc('thread/queue/add',{'threadId':attempt['runtime_id'],
            'input':[{'type':'text','text':self.job['message'],'text_elements':[]}],
            'clientUserMessageId':attempt['id']})
        item=reply['queuedSubmission']
        if item.get('clientUserMessageId')!=attempt['id']: raise ProtocolError('Conferma della coda non pertinente')
        return {'accepted':True,'transport_id':item['id'],'detail':'Accodato; la chat lo acquisisce quando può elaborarlo'}

    def reconcile(self, attempt):
        binding=json.loads(attempt['binding_json'])
        base={'threadId':attempt['runtime_id'],'limit':100,'sortDirection':'desc'}
        # Always inspect the head for newly arrived receipts; also advance the
        # previous cursor so a high-traffic history cannot hide an old receipt.
        page=self.client.rpc('thread/items/list',base)
        receipt=codex_page_receipt(attempt,page,time.time())
        if not receipt and binding.get('items_cursor'):
            page=self.client.rpc('thread/items/list',{**base,'cursor':binding['items_cursor']})
            receipt=codex_page_receipt(attempt,page,time.time())
        binding['items_cursor']=page.get('nextCursor')
        if receipt: return receipt,binding,None
        queued=self.client.rpc('thread/queue/list',{'threadId':attempt['runtime_id'],'limit':100})
        match=[x for x in queued['data'] if x.get('clientUserMessageId')==attempt['id']]
        return None,binding,('Accodato nella chat; attende l’elaborazione' if match else 'Ricezione non ancora verificata; nessun reinvio')

    def close(self):
        if self.client:self.client.close()


class ClaudeTransport:
    def __init__(self, job, config, root):
        self.client=None
        self.root=Path(root);self.job=job;self.config=config
        desktop=receiver_processes('claude',config)
        from claude_delivery import locate
        identity=locate(job)
        runtime=str(uuid.UUID(identity['session']))
        owners=[]
        children={}
        for parent in desktop:
            try:
                for proc in parent.children():children[proc.pid]=proc
            except psutil.Error:continue
        for proc in children.values():
            try:
                args=proc.cmdline()
                bound=('--resume='+runtime in args or any(arg=='--resume' and args[i+1]==runtime for i,arg in enumerate(args[:-1])))
                if bound:owners.append(proc)
            except psutil.Error: continue
        if len(owners)!=1: raise ProtocolError('Sessione Claude non aperta nel desktop: nessun invio')
        self.executable=owners[0].exe()
        args=owners[0].cmdline()
        modes=[args[i+1] for i,arg in enumerate(args[:-1]) if arg=='--permission-mode']
        modes += [arg.split('=',1)[1] for arg in args if arg.startswith('--permission-mode=')]
        # Only this genuine, tested permission-class combination is qualified.
        # Never change receiver permissions or spoof a bypassed sender's class.
        if modes not in (['default'],['acceptEdits']):
            raise ProtocolError('Policy Claude non qualificata per invio locale; permessi conservati')
        self.binding={'app':'claude','logical_id':job['session_id'],'runtime_id':runtime,
            'history':identity['history'],'history_offset':Path(identity['history']).stat().st_size,
            'index':identity['index'],'revision':1}
        if not job.get('send_when_active'):
            # No unreliable inference of idle from a producer or process alive.
            raise ProtocolError('Invio Claude a chat libera non ancora verificabile dal collegamento nativo')

    def prepare(self, attempt):
        from bounded_process import run_bounded
        plugin=SOURCE_ROOT/'assets/native_claude_bridge'
        validation=run_bounded([self.executable,'plugin','validate',str(plugin)],timeout=5,cwd=self.root)
        if validation['timed_out'] or validation['returncode']:
            raise ProtocolError('Collegamento Claude non compatibile con la versione installata; nessun invio')
        work=self.root/'logs/native-sender';work.mkdir(parents=True,exist_ok=True)
        self.binding=json.loads(attempt['binding_json'])
        self.binding['sender_name']='sentinella-'+attempt['id']
        self.client=OwnedJsonProcess([self.executable,'--print','--input-format','stream-json',
            '--output-format','stream-json','--verbose','--replay-user-messages',
            '--plugin-dir',str(plugin),
            '--permission-mode','default','--max-turns','2','--name',self.binding['sender_name']],work)
        # Official SDK handshake returns registered commands BEFORE any user
        # prompt. An absent/disabled module must never send a slash to a model.
        request_id=str(uuid.uuid4())
        self.client.send({'type':'control_request','request_id':request_id,
            'request':{'subtype':'initialize','hooks':None}})
        reply=self.client.receive_until(lambda value:value.get('type')=='control_response'
            and value.get('response',{}).get('request_id')==request_id,5)['response']
        capabilities=reply.get('response',{})
        commands={c.get('name') for c in capabilities.get('commands',[]) if isinstance(c,dict)}
        if (reply.get('subtype')!='success' or capabilities.get('current_permission_mode')!='default'
                or not {'sentinella-status','sentinella-send'}<=commands):
            raise ProtocolError('Componente Claude disabilitato o non disponibile; nessun messaggio a un modello')
        self.binding['cli_version']=capabilities.get('claude_code_version')
        self.command('/sentinella-status')
        init=None;ready=None
        deadline=time.monotonic()+7
        while not init or not ready:
            value=self.client.receive_until(lambda value:value.get('type') in {'system','result'},max(.1,deadline-time.monotonic()))
            if value.get('subtype')=='init':init=value
            if value.get('type')=='result':ready=self.result(value)
            if time.monotonic()>=deadline:raise ProtocolError('Collegamento Claude non pronto; nessun invio')
        if init.get('permissionMode')!='default' or ready.get('ready') is not True or ready.get('revision')!=1:
            raise ProtocolError('Capacità Claude non disponibile; nessun invio')

    def command(self,text):
        self.client.send({'type':'user','message':{'role':'user','content':text}})

    @staticmethod
    def result(value):
        if value.get('is_error'):raise ProtocolError('Il comando locale Claude è stato rifiutato')
        if value.get('num_turns')!=0:raise ProtocolError('Il collegamento locale non deve chiamare un modello')
        text=value.get('result','')
        marker='SENTINELLA_JSON:'
        if marker not in text:raise ProtocolError('Risposta Claude non compatibile; aggiorna il collegamento')
        return json.loads(text.split(marker,1)[1])

    def dispatch(self, attempt):
        if not self.guard():raise NotDispatched('Pausa, chiusura o modifica prima dell’invio')
        self.command('/sentinella-send '+json.dumps({'attempt':attempt['id'],
            'target':attempt['runtime_id'],'text':self.job['message']},ensure_ascii=False))
        answer=self.result(self.client.receive_until(lambda value:value.get('type')=='result',5))
        if answer.get('attempt')!=attempt['id'] or answer.get('target')!=attempt['runtime_id']:
            raise ProtocolError('Risposta Claude non pertinente')
        return {'accepted':answer.get('isDelivered') is True,
                'detail':'Accettato da Claude; verifica della ricezione' if answer.get('isDelivered') is True else 'Claude non conferma la consegna; nessun reinvio automatico'}

    def reconcile(self, attempt):
        receipt,cursor=claude_receipt(attempt)
        binding=json.loads(attempt['binding_json']);binding['history_offset']=cursor
        return receipt,binding,'Ricezione non ancora verificata; nessun reinvio'

    def close(self):
        if self.client:self.client.close()


def transport(job, config, root):
    return {'codex':CodexTransport,'claude':ClaudeTransport}[job['app']](job,config,root)
