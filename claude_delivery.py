"""Durable composer ownership and real user-message receipts for Claude timers.

Invoke is asynchronous. Never release a send reservation merely because it
returned, and never reuse an existing draft without a persisted ownership proof.
"""
import hashlib
import json
import os
from pathlib import Path
import time
import uuid
from datetime import datetime

from chat_catalog import claude_header


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def same_session(left, right):
    return left['session'] == right['session'] and os.path.normcase(left['index']) == os.path.normcase(right['index'])


def queued_text(content):
    """Read text-only Claude queue envelopes, excluding leading system blocks."""
    if isinstance(content, list):
        if not content or any(not isinstance(x, dict) or x.get('type') != 'text'
                              or not isinstance(x.get('text'), str) for x in content):
            return None
        parts = [queued_text(x['text']) for x in content]
        if any(part is None for part in parts):
            return None
        return ''.join(parts)
    if not isinstance(content, str):
        return None
    while content.startswith('<system-reminder>'):
        closing = content.find('</system-reminder>')
        if closing < 0:
            return None
        content = content[closing + len('</system-reminder>'):].lstrip('\r\n')
    return content


def received_text(record):
    if record.get('type') != 'user' or record.get('message', {}).get('role') != 'user':
        return None
    content = record['message'].get('content')
    if isinstance(content, str):
        return content
    # Tool results and text mixed with other content are not a sent prompt.
    if isinstance(content, list) and content and all(x.get('type') == 'text' for x in content):
        # Claude Desktop prepends separate system-reminder blocks to a real
        # human prompt. Their presence must not hide the literal user block.
        # Do not strip substrings or accept a match inside arbitrary prose.
        parts = [x.get('text', '') for x in content]
        authored = [text for text in parts if not (text.startswith('<system-reminder>') and text.rstrip().endswith('</system-reminder>'))]
        return ''.join(authored) if authored else None
    return None


def receipt_matches(expected, actual):
    # Claude removes terminal ASCII spaces on submit. No internal whitespace,
    # case, accents, line endings or leading characters may be changed.
    return actual == expected or expected.endswith(' ') and actual == expected.rstrip(' ')


def locate(job):
    local = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local'))
    roots = list((local / 'Claude-Data').glob('claude_*'))
    roots.append(Path(os.environ.get('APPDATA', Path.home() / 'AppData/Roaming')) / 'Claude')
    matches = []
    deadline = time.monotonic() + 3
    for root in roots:
        for n, path in enumerate((root / 'claude-code-sessions').glob('*/*/*.json')):
            if n >= 3000 or time.monotonic() >= deadline:
                raise ValueError('Indice Claude troppo ampio: identità non verificata')
            header = claude_header(path)
            bound=job.get('session_id')
            if header.get('isArchived') or (path.stem.removeprefix('local_')!=bound if bound else header.get('title') != job['title']):
                continue
            # cwd can change to another worktree inside the SAME session. It
            # does not identify the sidebar group. The native adapter verifies
            # the exact title/group; metadata must identify one exact session.
            if path.stat().st_size > 4 * 1024 * 1024:
                raise ValueError('Indice Claude troppo grande')
            metadata = json.loads(path.read_text(encoding='utf-8'))
            session = metadata.get('cliSessionId', '')
            try:
                uuid.UUID(session)
            except (ValueError, AttributeError):
                continue
            histories = list((Path.home() / '.claude/projects').glob('*/' + session + '.jsonl'))
            if len(histories) == 1:
                matches.append({'index': str(path), 'history': str(histories[0]), 'session': session})
    if len(matches) != 1:
        raise ValueError('Identità Claude non univoca: nessuna scrittura o invio')
    return matches[0]


def read_receipt(identity, expected, since, offset=0, until=None, queued_only=False, pending_only=False):
    path = Path(identity['history'])
    if not path.is_file() or path.stat().st_size < offset:
        return None
    enqueued, attachments, pending = [], {}, None
    with path.open('rb') as stream:
        stream.seek(offset)
        read = 0
        deadline = time.monotonic() + 3
        while read < 32 * 1024 * 1024 and time.monotonic() < deadline:
            line = stream.readline(2 * 1024 * 1024)
            if not line:
                break
            read += len(line)
            if not line.endswith(b'\n'):
                break  # An incomplete record never proves receipt.
            try:
                record = json.loads(line)
                at = datetime.fromisoformat(record.get('timestamp', '').replace('Z', '+00:00')).timestamp()
            except (ValueError, TypeError, AttributeError):
                continue
            if at < since or until is not None and at > until:
                continue
            if record.get('sessionId') != identity['session']:
                continue
            kind = record.get('type')
            if kind == 'queue-operation' and (receipt_matches(expected, queued_text(record.get('content')))
                                              or record.get('operation') == 'enqueue' and 'content' not in record):
                if record.get('operation') == 'enqueue':
                    enqueued.append((at, queued_text(record.get('content'))))
                    if receipt_matches(expected, queued_text(record.get('content'))):
                        pending = {'queued': True, 'at': at}
                elif record.get('operation') in {'remove', 'dequeue'}:
                    pending = None
                if record.get('operation') == 'remove' and record.get('reason') == 'absorbed_mid_turn':
                    key = (record.get('commandUuid'), record.get('deliveryId'))
                    candidate = attachments.get(key)
                    if candidate and candidate['at'] <= at <= candidate['at'] + 120 and not pending_only:
                        return {**candidate, 'absorbed_at': at}
            elif kind == 'queue-operation' and record.get('operation') == 'dequeue' and not record.get('content'):
                pending = None
            if kind == 'attachment' and not record.get('isSidechain'):
                command = record.get('attachment', {})
                if (isinstance(command, dict) and command.get('type') == 'queued_command' and command.get('humanTurn') is True
                        and isinstance(command.get('origin'), dict) and command['origin'].get('kind') == 'human' and command.get('commandMode') == 'prompt'
                        and record.get('uuid') and receipt_matches(expected, queued_text(command.get('prompt')))):
                    try:
                        command_at = datetime.fromisoformat(command['timestamp'].replace('Z', '+00:00')).timestamp()
                        for identifier in (command['source_uuid'], command['delivery_id'], record['uuid']):
                            uuid.UUID(identifier)
                    except (KeyError, ValueError, TypeError, AttributeError):
                        continue
                    # Claude's queue envelope can be serialized one millisecond
                    # after the command timestamp. Keep the exact human text,
                    # session, envelope/command timestamp and absorption UUID
                    # chain mandatory. Only one matching enqueue within 10 ms
                    # may authorize this attachment; duplicates are ambiguous.
                    matching_enqueues = [queued_at for queued_at, text in enqueued
                                         if abs(queued_at - command_at) <= .010
                                         and (text is None or queued_text(command['prompt']) == text)]
                    if len(matching_enqueues) == 1 and at == command_at:
                        actual = queued_text(command['prompt'])
                        attachments[(command['source_uuid'], command['delivery_id'])] = {
                            'uuid': record['uuid'], 'at': at, 'text': actual, 'session': identity['session'],
                            'history': str(path), 'configured_sha256': digest(expected), 'received_sha256': digest(actual),
                            'match': 'exact' if actual == expected else 'claude_terminal_space_trim',
                            'origin': 'human_mid_turn', 'command_uuid': command['source_uuid'],
                            'delivery_id': command['delivery_id'], 'enqueue_at': matching_enqueues[0]}
                        pending = {'queued': True, 'at': at}
            if queued_only:
                if record.get('sessionId') == identity['session'] and record.get('type') == 'queue-operation' and record.get('operation') == 'enqueue' and receipt_matches(expected, record.get('content')):
                    return {'queued': True, 'at': at}
                continue
            if pending_only:
                continue
            actual = received_text(record)
            if record.get('sessionId') != identity['session'] or not record.get('uuid') or actual is None:
                continue
            if receipt_matches(expected, actual):
                return {'uuid': record['uuid'], 'at': at, 'text': actual,
                        'session': identity['session'], 'history': str(path),
                        'configured_sha256': digest(expected), 'received_sha256': digest(actual),
                        'match': 'exact' if actual == expected else 'claude_terminal_space_trim'}
    return pending if pending_only or queued_only else None


def read_modified_submission(identity, expected, since, offset):
    """Resolve a proven edited submission without calling it an exact receipt.

    A short appended edit must appear in enqueue -> dequeue -> user records
    immediately after this reservation's Invoke. No similar assistant text or
    later user prompt can release a reservation. This attempt is never retried.
    """
    queued = None
    dequeued = False
    with Path(identity['history']).open('rb') as stream:
        stream.seek(offset)
        data = stream.read(2 * 1024 * 1024)
    for line in data.splitlines(keepends=True):
        if not line.endswith(b'\n'):
            break
        try:
            record = json.loads(line)
            at = datetime.fromisoformat(record.get('timestamp', '').replace('Z', '+00:00')).timestamp()
        except (ValueError, TypeError, AttributeError):
            continue
        if record.get('sessionId') != identity['session'] or not since <= at <= since + 2:
            continue
        if record.get('type') == 'queue-operation':
            if record.get('operation') == 'enqueue':
                if queued is not None:
                    return None
                actual = record.get('content')
                if not isinstance(actual, str) or not actual.startswith(expected.rstrip(' ')) or not 0 < len(actual) - len(expected.rstrip(' ')) <= 32 or receipt_matches(expected, actual):
                    return None
                queued = actual
            elif record.get('operation') == 'dequeue' and queued is not None:
                dequeued = True
        actual = received_text(record)
        if queued is not None and dequeued and actual == queued and record.get('uuid'):
            return {'uuid': record['uuid'], 'at': at, 'text': actual,
                    'session': identity['session'], 'history': identity['history'],
                    'configured_sha256': digest(expected), 'received_sha256': digest(actual),
                    'match': 'modified_submission_NOT_configured_receipt'}
    return None


class Delivery:
    def __init__(self, store, root):
        self.store = store
        self.root = Path(root)
        self.db = store.db
        self.db.execute('''CREATE TABLE IF NOT EXISTS claude_timer_deliveries (
            token TEXT PRIMARY KEY, job_id TEXT, version TEXT, title TEXT, project TEXT,
            message TEXT, message_hash TEXT, identity TEXT, prepared_at REAL,
            send_started REAL, history_offset INTEGER, stage TEXT, receipt TEXT,
            receipt_uuid TEXT UNIQUE, backup TEXT)''')
        self.db.commit()

    def current(self, job):
        row = self.db.execute('SELECT * FROM claude_timer_deliveries WHERE job_id=? AND version=? ORDER BY prepared_at DESC LIMIT 1', (job['id'], job['version'])).fetchone()
        return dict(row) if row else None

    def prepare(self, job):
        previous = self.current(job)
        if previous and previous['stage'] == 'prepared' and previous['send_started'] is None and previous['message_hash'] == digest(job['message']):
            if not same_session(locate(job), json.loads(previous['identity'])):
                raise ValueError('Identità della bozza cambiata: nessuna riscrittura')
            return  # This intent has provably never reached Invoke.
        if previous and previous['stage'] not in {'cleared', 'draft_preserved'}:
            raise ValueError('Prenotazione Claude già presente: nessun duplicato')
        identity = locate(job)
        token = str(uuid.uuid4())
        self.db.execute('INSERT INTO claude_timer_deliveries(token,job_id,version,title,project,message,message_hash,identity,prepared_at,history_offset,stage) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                        (token, job['id'], job['version'], job['title'], job['group'], job['message'], digest(job['message']), json.dumps(identity), time.time(), Path(identity['history']).stat().st_size, 'prepared'))
        self.db.commit()

    def begin_send(self, job):
        row = self.current(job)
        if not row or row['stage'] != 'prepared' or row['message_hash'] != digest(job['message']):
            raise ValueError('Invio Claude privo di bozza prenotata')
        identity = locate(job)
        if not same_session(identity, json.loads(row['identity'])):
            raise ValueError('Identità Claude cambiata prima dell’invio')
        self.db.execute('UPDATE claude_timer_deliveries SET send_started=?,history_offset=?,stage=?,identity=? WHERE token=?',
                        (time.time(), Path(identity['history']).stat().st_size, 'sending', json.dumps(identity), row['token']))
        self.db.commit()
        return True

    def reconcile(self, job, state):
        row = self.current(job)
        if row is None:
            return self.adopt_legacy(job, state)
        if row['stage'] == 'prepared' and row['send_started'] is None:
            identity = locate(job)
            old_identity = json.loads(row['identity'])
            if not same_session(identity, old_identity):
                return {'action': 'awaiting_receipt', 'reason': 'Sessione della bozza cambiata: nessun invio'}
            offset = (row['history_offset'] or 0) if identity['history'] == old_identity['history'] else 0
            receipt = read_receipt(identity, row['message'], row['prepared_at'] - .001, offset)
            if receipt:
                receipt['origin'] = 'owned_draft_submitted_outside_sender'
                self.confirm(job, row, receipt)
                return {'action': 'receipt_confirmed', 'receipt': receipt}
            if read_receipt(identity, row['message'], row['prepared_at'] - .001, offset, queued_only=True):
                return {'action': 'awaiting_receipt', 'reason': 'Bozza già accodata nella sessione: nessun nuovo invio'}
        if row['stage'] == 'sending':
            identity = locate(job)
            old_identity = json.loads(row['identity'])
            if not same_session(identity, old_identity):
                return {'action': 'awaiting_receipt', 'reason': 'Identità della ricevuta cambiata: nessun reinvio'}
            offset = row['history_offset'] if identity['history'] == old_identity['history'] else 0
            receipt = read_receipt(identity, row['message'], row['send_started'] - 1, offset)
            if receipt:
                self.confirm(job, row, receipt)
                return {'action': 'receipt_confirmed', 'receipt': receipt}
            modified = read_modified_submission(identity, row['message'], row['send_started'], offset)
            if modified:
                self.db.execute('UPDATE claude_timer_deliveries SET stage=?,receipt=?,receipt_uuid=? WHERE token=?',
                                ('draft_preserved', json.dumps(modified, ensure_ascii=False), modified['uuid'], row['token']))
                # Consumed Invoke with a proven user edit, not a successful
                # configured delivery. Keep last_sent unchanged; no immediate
                # retry or catch-up burst, only the following regular slot.
                self.db.execute('UPDATE timers SET phase=?,due=?,detail=? WHERE id=? AND version=?',
                                ('idle', row['send_started'] + job['interval_seconds'],
                                 'Testo modificato durante l’invio: tentativo non ripetuto; prossima scadenza regolare', job['id'], job['version']))
                self.db.execute('INSERT INTO message_events(at,job_id,title,event,detail) VALUES (?,?,?,?,?)',
                                (modified['at'], job['id'], job['title'], 'Invio da verificare · testo modificato',
                                 'Ricevuto un testo modificato dopo la preparazione. Nessuna conferma del testo configurato, nessun reinvio del tentativo.'))
                self.db.commit()
                return {'action': 'modified_submission', 'reason': 'Modifica del testo documentata: tentativo consumato, nessun reinvio'}
            queued = read_receipt(identity, row['message'], row['send_started'] - 1, offset, pending_only=True)
            detail = ('Messaggio accodato alla chat: attendo che venga elaborato' if queued else
                      'Ricezione non confermata: nessun reinvio automatico')
            if state.get('detail') != detail:
                self.store.update(job, 'uncertain', detail)
            if queued:
                return {'action': 'awaiting_receipt', 'reason': detail}
            return {'action': 'awaiting_receipt', 'reason': 'Ricezione Claude da verificare: nessun reinvio automatico'}
        if row['stage'] == 'prepared' and state['phase'] == 'uncertain' and row['send_started'] is None:
            # A failed preflight committed no send marker, hence Invoke could
            # not have run. Keep the owned draft and retry only the preflight.
            self.store.update(job, 'drafted', 'Bozza prenotata recuperata prima del pulsante Invia')
            return {'action': 'draft_recovered'}
        return None

    def confirm(self, job, row, receipt, legacy=False):
        # A UUID can confirm only one reservation, including after a restart.
        self.db.execute('UPDATE claude_timer_deliveries SET stage=?,receipt=?,receipt_uuid=? WHERE token=?',
                        ('confirmed', json.dumps(receipt, ensure_ascii=False), receipt['uuid'], row['token']))
        if not legacy:
            sent = row['send_started'] if row['send_started'] is not None else receipt['at']
            self.db.execute('UPDATE timers SET phase=?,due=?,last_sent=?,detail=? WHERE id=? AND version=?',
                            ('idle' if job.get('repeat', True) else 'finished', sent + job['interval_seconds'], sent,
                             'Consegna verificata nella cronologia Claude', job['id'], job['version']))
        self.db.execute('INSERT INTO message_events(at,job_id,title,event,detail) VALUES (?,?,?,?,?)',
                        (receipt['at'], job['id'], job['title'], 'Consegnato con ricevuta',
                         'Messaggio umano ' + receipt['uuid']
                         + ('; ricevuto durante il lavoro, accodamento e assorbimento verificati' if receipt.get('origin') == 'human_mid_turn' else '')
                         + ('; Claude rimuove lo spazio finale' if receipt['match'] != 'exact' else '')))
        self.db.commit()

    def adopt_legacy(self, job, state):
        if state['version'] != job['version'] or not state.get('last_sent'):
            return None
        sent = state['last_sent']
        # Recover only this release's known prepare -> reservation -> send chain.
        rows = list(self.db.execute('SELECT event,at FROM message_events WHERE job_id=? AND at>=? AND at<=? ORDER BY seq',
                                   (job['id'], job.get('activated_at', sent), sent + 1)))
        if len(rows) < 3 or not rows[-1]['event'].startswith('Invio effettuato') or abs(rows[-1]['at'] - sent) > 1 or rows[-2]['event'] != 'Da verificare' or rows[-3]['event'] != 'Preparazione':
            return None
        identity = locate(job)
        receipt = read_receipt(identity, job['message'], rows[-2]['at'] - 1, until=sent + 10)
        if not receipt:
            return None
        token = str(uuid.uuid4())
        self.db.execute('INSERT INTO claude_timer_deliveries(token,job_id,version,title,project,message,message_hash,identity,prepared_at,send_started,stage) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                        (token, job['id'], job['version'], job['title'], job['group'], job['message'], digest(job['message']), json.dumps(identity), rows[-3]['at'], rows[-2]['at'], 'sending'))
        self.db.commit()
        self.confirm(job, self.current(job), receipt, legacy=True)
        return {'action': 'receipt_confirmed', 'receipt': receipt}

    def archive(self, job, value):
        row = self.current(job)
        if (not row or row['stage'] not in {'confirmed', 'draft_preserved'} or not row['receipt']
                or not receipt_matches(row['message'], value) or digest(row['message']) != row['message_hash']):
            raise ValueError('Bozza priva di proprietà e ricevuta: conservata nel campo')
        if not receipt_matches(row['message'], json.loads(row['receipt']).get('text')):
            raise ValueError('Ricevuta di un testo diverso: bozza conservata nel campo')
        if not same_session(locate(job), json.loads(row['identity'])):
            raise ValueError('Identità della bozza cambiata: conservata nel campo')
        path = self.root / 'logs' / 'preserved-drafts' / (row['token'] + '.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {'job_id': job['id'], 'version': job['version'], 'title': job['title'],
                   'identity': json.loads(row['identity']), 'text': value, 'sha256': digest(value),
                   'receipt': json.loads(row['receipt']), 'archived_at': time.time(),
                   'reason': 'Residuo della bozza di Sentinella già ricevuta; copia integrale prima della pulizia'}
        if not path.exists():
            with path.open('x', encoding='utf-8') as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
        saved = json.loads(path.read_text(encoding='utf-8'))
        if saved['text'] != value or saved['sha256'] != digest(value):
            raise ValueError('Copia di recupero non valida: bozza conservata nel campo')
        self.db.execute('UPDATE claude_timer_deliveries SET backup=? WHERE token=?', (str(path), row['token']))
        self.db.commit()

    def cleanup_needed(self, job):
        row = self.current(job)
        return row is not None and (row['stage'] == 'confirmed' or
                                    row['stage'] == 'draft_preserved' and row['receipt_uuid'] is not None
                                    and receipt_matches(row['message'], json.loads(row['receipt']).get('text')))

    def cancel_before_send(self, job):
        row = self.current(job)
        if row and row['stage'] in {'prepared', 'sending'}:
            # Called only before Invoke. Keep the owned preparation reusable:
            # typing during validation must not turn our draft into a foreign one.
            self.db.execute("UPDATE claude_timer_deliveries SET stage='prepared',send_started=NULL WHERE token=?", (row['token'],))
            self.db.commit()

    def cleanup_done(self, job, action):
        if action not in {'draft_cleared', 'draft_preserved'}:
            return
        self.db.execute('UPDATE claude_timer_deliveries SET stage=? WHERE token=?',
                        ('cleared' if action == 'draft_cleared' else 'draft_preserved', self.current(job)['token']))
        self.db.commit()
