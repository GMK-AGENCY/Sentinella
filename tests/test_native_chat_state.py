from pathlib import Path
from contextlib import closing
import sqlite3
import os
import tempfile
import unittest
from unittest.mock import patch
import uuid

from native_chat_state import Activity, codex_chat_state


class CodexChatStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.thread = str(uuid.uuid4()); self.turn = str(uuid.uuid4())
        self.rollout = self.home / 'sessions' / ('rollout-test-' + self.thread + '.jsonl')
        self.rollout.parent.mkdir(); self.rollout.write_bytes(b'x' * 100)
        self.state = self.home / 'state_5.sqlite'
        self.history = self.home / 'thread_history_1.sqlite'
        with closing(sqlite3.connect(self.state)) as db, db:
            db.execute('CREATE TABLE threads(id TEXT,rollout_path TEXT,history_mode TEXT,archived INTEGER)')
            db.execute('INSERT INTO threads VALUES(?,?,?,?)', (self.thread, str(self.rollout), 'paginated', 0))
        with closing(sqlite3.connect(self.history)) as db, db:
            db.execute('CREATE TABLE thread_history_projection_state(thread_id TEXT,next_rollout_byte_offset INTEGER,next_rollout_ordinal INTEGER)')
            db.execute('INSERT INTO thread_history_projection_state VALUES(?,?,?)', (self.thread, 100, 10))
            db.execute('CREATE TABLE thread_turns(thread_id TEXT,turn_id TEXT,rollout_ordinal INTEGER,status TEXT,started_at INTEGER,completed_at INTEGER,rollout_byte_offset INTEGER,rollout_end_ordinal INTEGER,rollout_end_byte_offset INTEGER,root_turn_id TEXT)')
            db.execute('INSERT INTO thread_turns VALUES(?,?,?,?,?,?,?,?,?,?)', (self.thread, self.turn, 2, 'completed', 1000, 1001, 20, 9, 100, self.turn))

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, **kwargs):
        return codex_chat_state(self.thread, server_version=kwargs.pop('server_version', 'Codex Desktop/0.166.0-alpha.5.1 (Windows)'), codex_home=self.home, **kwargs)

    def change(self, sql, args=()):
        with closing(sqlite3.connect(self.history)) as db, db: db.execute(sql, args)

    def test_coherent_terminal_states_are_raw_not_normalized(self):
        for status in ('completed', 'interrupted', 'failed'):
            self.change('UPDATE thread_turns SET status=?', (status,))
            observed = self.read()
            self.assertEqual(Activity.IDLE, observed.state)
            self.assertEqual(self.turn, observed.turn_id)

    def test_unfinished_raw_turn_is_conservative_veto_even_after_owner_crash(self):
        self.change('UPDATE thread_turns SET status=?,completed_at=NULL,rollout_end_ordinal=NULL,rollout_end_byte_offset=NULL', ('inProgress',))
        self.assertEqual(Activity.ACTIVE, self.read().state)

    def test_projection_lag_and_truncation_are_unknown(self):
        for size in (99, 101):
            self.rollout.write_bytes(b'x' * size)
            self.assertEqual(Activity.UNKNOWN, self.read().state)

    def test_no_projection_no_turn_or_wrong_target_never_means_idle(self):
        self.assertEqual(Activity.UNKNOWN, codex_chat_state(str(uuid.uuid4()), server_version='0.166.0-alpha.5.1', codex_home=self.home).state)
        self.change('DELETE FROM thread_history_projection_state')
        self.assertEqual(Activity.UNKNOWN, self.read().state)
        self.change('INSERT INTO thread_history_projection_state VALUES(?,?,?)', (self.thread, 100, 10))
        self.change('DELETE FROM thread_turns')
        self.assertEqual(Activity.UNKNOWN, self.read().state)

    def test_future_enum_or_runtime_version_fails_closed(self):
        for version in ('0.166.0-alpha.5.10', '0.167.0', '', None):
            self.assertEqual(Activity.UNKNOWN, self.read(server_version=version).state)
        self.change('UPDATE thread_turns SET status=?', ('waitingForProvider',))
        self.assertEqual(Activity.UNKNOWN, self.read().state)

    def test_new_database_generation_is_not_silently_ignored(self):
        (self.home / 'state_6.sqlite').touch()
        self.assertEqual(Activity.UNKNOWN, self.read().state)

    def test_changed_or_replaced_canonical_file_is_unknown(self):
        import native_chat_state
        original = native_chat_state._fingerprint
        calls = []
        def changing(path):
            value = original(path); calls.append(value)
            return value if len(calls) == 1 else (*value[:3], value[3] + 1, value[4])
        with patch('native_chat_state._fingerprint', side_effect=changing):
            self.assertEqual(Activity.UNKNOWN, self.read().state)

    def test_foreign_or_compressed_rollout_is_unknown(self):
        for value in (str(self.home / 'foreign.jsonl'), str(self.rollout) + '.zst'):
            Path(value).write_bytes(b'x' * 100)
            with closing(sqlite3.connect(self.state)) as db, db: db.execute('UPDATE threads SET rollout_path=?', (value,))
            self.assertEqual(Activity.UNKNOWN, self.read().state)

    def test_archived_legacy_and_unknown_schema_fail_closed(self):
        with closing(sqlite3.connect(self.state)) as db, db: db.execute('UPDATE threads SET archived=1')
        self.assertEqual(Activity.UNKNOWN, self.read().state)
        with closing(sqlite3.connect(self.state)) as db, db: db.execute('UPDATE threads SET archived=0,history_mode=?', ('legacy',))
        self.assertEqual(Activity.UNKNOWN, self.read().state)
        with closing(sqlite3.connect(self.state)) as db, db: db.execute('UPDATE threads SET history_mode=?', ('paginated',))
        self.change('ALTER TABLE thread_turns RENAME COLUMN status TO unknown_status')
        self.assertEqual(Activity.UNKNOWN, self.read().state)

    def test_inconsistent_terminal_or_older_open_turn_is_unknown(self):
        self.change('UPDATE thread_turns SET completed_at=NULL')
        self.assertEqual(Activity.UNKNOWN, self.read().state)
        self.change('UPDATE thread_turns SET completed_at=1001')
        self.change('INSERT INTO thread_turns VALUES(?,?,?,?,?,?,?,?,?,?)', (self.thread, str(uuid.uuid4()), 1, 'inProgress', 999, None, 1, None, None, self.turn))
        self.assertEqual(Activity.UNKNOWN, self.read().state)

    def test_reads_neither_bodies_nor_creates_missing_databases(self):
        with patch.object(Path, 'open', side_effect=AssertionError('No rollout body reads')):
            self.assertEqual(Activity.IDLE, self.read().state)
        missing = self.home / 'missing.sqlite'
        self.assertEqual(Activity.UNKNOWN, self.read(history_path=missing).state)
        self.assertFalse(missing.exists())

    @unittest.skipUnless(os.name == 'nt', 'Windows native extended paths')
    def test_native_extended_windows_path_matches_ordinary_home(self):
        with closing(sqlite3.connect(self.state)) as db, db:
            db.execute('UPDATE threads SET rollout_path=?', ('\\\\?\\' + str(self.rollout),))
        self.assertEqual(Activity.IDLE, self.read().state)


if __name__ == '__main__':
    unittest.main()
