"""Conservative, read-only Codex activity snapshots for the qualified build.

This is a versioned storage adapter, not a live-owner RPC. A new turn can begin
after a snapshot; the native queue consumer still admits input only when idle.
Never infer idle from a missing row, an API-normalized interrupted turn, or a
SQLite projection which has not caught up with the canonical JSONL file.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid


class Activity(str, Enum):
    IDLE = 'idle'
    ACTIVE = 'active'
    UNKNOWN = 'unknown'


@dataclass(frozen=True)
class ChatState:
    state: str
    reason: str
    turn_id: str | None = None
    code: str = 'unavailable'


_BUILD = re.compile(r'(?<![\w.-])0\.166\.0-alpha\.5\.1(?![\w.-])')
_STATUSES = {'inProgress', 'completed', 'interrupted', 'failed'}
_TURN_COLUMNS = {'thread_id', 'turn_id', 'rollout_ordinal', 'status',
                 'started_at', 'completed_at', 'rollout_byte_offset',
                 'rollout_end_ordinal', 'rollout_end_byte_offset', 'root_turn_id'}


def _unknown(reason, code='unavailable'):
    return ChatState(Activity.UNKNOWN.value, reason,code=code)


@contextmanager
def _read_database(path):
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=.2)
    deadline = time.monotonic() + .3
    db.set_progress_handler(lambda: time.monotonic() > deadline, 1000)
    try:
        db.execute('PRAGMA query_only=ON')
        yield db
    finally:
        db.close()


def _columns(db, table):
    # Table names are internal constants, never chat data.
    return {row[1] for row in db.execute(f'PRAGMA table_info({table})')}


def _current_database(home, prefix, version):
    candidates = [(int(match[1]), path) for path in home.glob(prefix + '_*.sqlite')
                  if (match := re.fullmatch(re.escape(prefix) + r'_(\d+)\.sqlite', path.name))]
    if not candidates or max(n for n, _ in candidates) != version:
        raise ValueError('Versione dell’archivio Codex non qualificata')
    return next(path for n, path in candidates if n == version)


def _fingerprint(path):
    stat = path.stat()
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def _resolved(path, *, strict=False):
    value = str(Path(path).resolve(strict=strict))
    # Native Codex stores extended Windows paths; pathlib keeps their prefix,
    # which otherwise prevents comparison with the same ordinary CODEX_HOME.
    if os.name == 'nt':
        if value.startswith('\\\\?\\UNC\\'):
            value = '\\\\' + value[8:]
        elif value.startswith('\\\\?\\'):
            value = value[4:]
    return Path(value)


def _canonical_rollout(home, raw_path, thread_id):
    path = _resolved(raw_path, strict=True)
    roots = [_resolved(home / name) for name in ('sessions', 'archived_sessions')]
    if (not path.is_file() or not any(path.is_relative_to(root) for root in roots)
            or not path.name.endswith('-' + thread_id + '.jsonl')):
        raise ValueError('Rollout canonico non qualificato per questo ID')
    return path


def codex_chat_state(thread_id, *, server_version, codex_home=None,
                     state_path=None, history_path=None):
    """Return idle/active/unknown without reading any chat body or owning a turn.

    ``server_version`` must come from the current native initialize response,
    not the thread's historical cli_version. Explicit database paths support
    isolated fixtures; runtime callers normally use the resolved CODEX_HOME.
    Only a coherent, fully projected raw terminal turn permits ``idle``.
    ``active`` is a conservative veto: a crashed owner's unfinished turn may
    remain active until Codex records its native terminal event.
    """
    if not isinstance(server_version, str) or not _BUILD.search(server_version):
        return _unknown('Versione Codex non qualificata per leggere lo stato')
    try:
        if not isinstance(thread_id, str) or str(uuid.UUID(thread_id)) != thread_id:
            return _unknown('ID Codex non valido')
        home = _resolved(codex_home or os.environ.get('CODEX_HOME', Path.home() / '.codex'))
        state = Path(state_path) if state_path is not None else _current_database(home, 'state', 5)
        history = Path(history_path) if history_path is not None else _current_database(home, 'thread_history', 1)
        with _read_database(state) as metadata:
            if not {'id', 'rollout_path', 'history_mode', 'archived'} <= _columns(metadata, 'threads'):
                return _unknown('Formato dei metadati Codex non qualificato')
            row = metadata.execute('SELECT id,rollout_path,history_mode,archived FROM threads WHERE id=?',
                                   (thread_id,)).fetchone()
            if not row or row[0] != thread_id or row[2] != 'paginated' or row[3] != 0:
                return _unknown('Chat assente, archiviata o archivio non qualificato')
            rollout = _canonical_rollout(home, row[1], thread_id)
            before = _fingerprint(rollout)
            with _read_database(history) as archive:
                archive.execute('BEGIN')
                if (not _TURN_COLUMNS <= _columns(archive, 'thread_turns')
                        or not {'thread_id', 'next_rollout_byte_offset', 'next_rollout_ordinal'}
                        <= _columns(archive, 'thread_history_projection_state')):
                    return _unknown('Formato dello stato dei turni Codex non qualificato')
                projection = archive.execute('SELECT next_rollout_byte_offset,next_rollout_ordinal '
                    'FROM thread_history_projection_state WHERE thread_id=?', (thread_id,)).fetchone()
                turn = archive.execute('SELECT turn_id,rollout_ordinal,status,started_at,completed_at,'
                    'rollout_byte_offset,rollout_end_ordinal,rollout_end_byte_offset '
                    'FROM thread_turns WHERE thread_id=? ORDER BY rollout_ordinal DESC LIMIT 1',
                    (thread_id,)).fetchone()
                statuses = dict(archive.execute('SELECT status,count(*) FROM thread_turns '
                                               'WHERE thread_id=? GROUP BY status', (thread_id,)))
                after = _fingerprint(rollout)
            final_row = metadata.execute('SELECT id,rollout_path,history_mode,archived FROM threads WHERE id=?',
                                         (thread_id,)).fetchone()
            if row != final_row or before != after:
                return _unknown('La cronologia Codex è cambiata durante il controllo','updating')
            if projection and type(projection[0]) is int and 0<projection[0]<after[2]:
                return _unknown('La cronologia Codex sta sincronizzando gli ultimi eventi','updating')
            if (not projection or not turn or type(projection[0]) is not int
                    or type(projection[1]) is not int or projection[0] != after[2]
                    or projection[0] <= 0 or projection[1] <= 0):
                return _unknown('Cronologia Codex non ancora sincronizzata')
            turn_id, ordinal, status, started, completed, start_offset, end_ordinal, end_offset = turn
            if (not isinstance(turn_id, str) or str(uuid.UUID(turn_id)) != turn_id or not set(statuses) <= _STATUSES
                    or type(ordinal) is not int or not 0 <= ordinal < projection[1]
                    or type(started) is not int or started <= 0
                    or type(start_offset) is not int or not 0 <= start_offset < projection[0]):
                return _unknown('Stato del turno Codex incompleto o non qualificato')
            if status == 'inProgress':
                if statuses.get('inProgress') != 1 or any(x is not None for x in (completed, end_ordinal, end_offset)):
                    return _unknown('Turni Codex non coerenti')
                return ChatState(Activity.ACTIVE.value, 'La chat ha un turno non ancora concluso', turn_id)
            if (statuses.get('inProgress', 0) or type(completed) is not int or completed < started
                    or type(end_ordinal) is not int or not ordinal <= end_ordinal < projection[1]
                    or type(end_offset) is not int or not start_offset < end_offset <= projection[0]):
                return _unknown('Conclusione del turno Codex non verificabile')
            return ChatState(Activity.IDLE.value, 'Ultimo turno concluso nella cronologia sincronizzata', turn_id)
    except (OSError, ValueError, TypeError, sqlite3.Error):
        return _unknown('Stato Codex non disponibile; nessuna deduzione di chat libera')
