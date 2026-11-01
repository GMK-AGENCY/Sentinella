"""Owned, hidden JSONL clients. No GUI, shell interpolation or credential copying."""
import json
import queue
import subprocess
import tempfile
import threading
import time

from bounded_process import stop_owned
import psutil


class ProtocolError(RuntimeError):
    pass


class OwnedJsonProcess:
    def __init__(self, command, cwd, env=None):
        self.messages = queue.Queue()
        self.stderr = tempfile.TemporaryFile()
        self.process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=self.stderr,
            env=env,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.owned = {self.process.pid: psutil.Process(self.process.pid)}
        self.owned[self.process.pid].create_time()
        self.closed = False
        self.request_id = 0
        def read():
            try:
                while True:
                    line = self.process.stdout.readline(16*1024*1024)
                    if not line: break
                    if not line.endswith(b'\n'):
                        self.messages.put(ProtocolError('Risposta del collegamento troppo grande'))
                        break
                    try: self.messages.put(json.loads(line))
                    except ValueError:
                        self.messages.put(ProtocolError('Risposta del collegamento non valida'))
                        break
            finally: self.messages.put(ProtocolError('Il processo del collegamento si è chiuso'))
        threading.Thread(target=read,daemon=True).start()

    def send(self, message):
        if self.closed: raise ProtocolError('Collegamento chiuso')
        done=queue.Queue(maxsize=1)
        def write():
            try:
                self.process.stdin.write((json.dumps(message,ensure_ascii=False)+'\n').encode('utf-8'))
                self.process.stdin.flush()
                done.put(None)
            except (OSError,ValueError) as exc:done.put(exc)
        threading.Thread(target=write,daemon=True).start()
        try:error=done.get(timeout=2)
        except queue.Empty:raise TimeoutError('Scrittura al collegamento bloccata; esito incerto') from None
        if error:raise ProtocolError('Collegamento interrotto durante la scrittura') from error

    def receive_until(self, predicate, timeout=5):
        deadline = time.monotonic()+timeout
        while True:
            remaining=deadline-time.monotonic()
            if remaining<=0: raise TimeoutError('Collegamento senza esito: nessun reinvio automatico')
            try:
                parent=psutil.Process(self.process.pid)
                for child in parent.children(recursive=True): self.owned[child.pid]=child
            except psutil.Error: pass
            try: value=self.messages.get(timeout=min(.1,remaining))
            except queue.Empty: continue
            if isinstance(value,Exception): raise value
            # Interactive requests are never handled by this worker.
            if value.get('type')=='control_request':
                self.send({'type':'control_response','response':{'subtype':'error',
                    'request_id':value.get('request_id'),'error':'Il mittente locale non approva richieste interattive'}})
                raise ProtocolError('Il collegamento richiede un intervento; nessun consenso automatico')
            if 'id' in value and 'method' in value:
                self.send({'id':value['id'],'error':{'code':-32601,'message':'Interazione non supportata dal mittente in background'}})
                raise ProtocolError('Il collegamento richiede un intervento; nessun consenso automatico')
            if predicate(value): return value

    def rpc(self, method, params, timeout=5):
        if method not in {'initialize','thread/loaded/list','thread/read','thread/items/list','thread/queue/add','thread/queue/list'}:
            raise ValueError('Metodo non ammesso per il mittente nativo')
        self.request_id+=1
        request_id=self.request_id
        self.send({'id':request_id,'method':method,'params':params})
        reply=self.receive_until(lambda value:value.get('id')==request_id,timeout)
        if 'error' in reply:
            error=reply['error']
            raise ProtocolError('Collegamento non disponibile: '+str(error.get('message','Errore del protocollo'))[:600])
        return reply['result']

    def close(self):
        if self.closed:return
        self.closed=True
        try:
            # Terminate only owned processes before closing a pipe that could
            # still have a blocked writer in the bounded send thread.
            stop_owned(self.process,self.owned)
            self.process.stdin.close()
        except (OSError,ValueError): pass
        finally:
            self.process.stdout.close()
            self.stderr.close()

    def __enter__(self): return self
    def __exit__(self,*args): self.close()
