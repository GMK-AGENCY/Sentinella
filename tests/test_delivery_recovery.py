import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

from delivery_recovery import apply_resolution, list_legacy_pending, RecoveryConflict
from message_engine import TimerStore


class DeliveryRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        (self.root / 'logs').mkdir()
        self.store = TimerStore(self.root / 'logs/messages.sqlite3')
        self.job = {'id': str(uuid.uuid4()), 'version': str(uuid.uuid4()), 'app': 'codex',
                    'thread_id': str(uuid.uuid4()), 'title': 'Titolo non identificante',
                    'group': 'Test', 'message': 'Testo intatto', 'enabled': False,
                    'repeat': True, 'interval_seconds': 60}
        self.token = str(uuid.uuid4())
        self.save()
        d = self.store.db
        d.execute('CREATE TABLE connector_deliveries(token TEXT PRIMARY KEY,job_id TEXT,version TEXT,thread_id TEXT,message_hash TEXT,reserved_at REAL,confirmed_at REAL,receipt TEXT)')
        d.execute('CREATE TABLE local_codex_deliveries(token TEXT,stage TEXT)')
        d.execute('INSERT INTO connector_deliveries VALUES(?,?,?,?,?,?,NULL,NULL)',
                  (self.token, self.job['id'], 'old-version', self.job['thread_id'], 'original-hash', 900))
        d.execute('INSERT INTO local_codex_deliveries VALUES(?,?)', (self.token, 'drafted'))
        d.execute('INSERT INTO timers VALUES(?,?,?,?,?,?)', (self.job['id'], self.job['version'], 1000, 'uncertain', 800, 'Originale'))
        d.commit()

    def tearDown(self):
        self.store.db.close(); self.tmp.cleanup()

    def save(self):
        (self.root / 'messages-config.json').write_text(json.dumps({'jobs': [self.job]}), encoding='utf-8')

    def apply(self, now=1120, expected=None):
        return apply_resolution(self.root, self.job['id'], self.job['version'],
                                [self.token] if expected is None else expected, now)

    def timer(self):
        return dict(self.store.db.execute('SELECT * FROM timers').fetchone())

    def test_future_only_preserves_legacy_config_receipts_and_last_sent(self):
        config = (self.root / 'messages-config.json').read_bytes()
        legacy = tuple(self.store.db.execute('SELECT * FROM connector_deliveries').fetchone())
        self.assertEqual([self.token], [x['token'] for x in list_legacy_pending(self.store, self.job)])
        result = self.apply()
        self.assertEqual(1180, result['next_due'])  # anchored 1000 + 3*60, not now+60 by accident
        self.assertEqual(800, self.timer()['last_sent']); self.assertEqual('idle', self.timer()['phase'])
        self.assertEqual(config, (self.root / 'messages-config.json').read_bytes())
        self.assertEqual(legacy, tuple(self.store.db.execute('SELECT * FROM connector_deliveries').fetchone()))
        self.assertEqual('drafted', self.store.db.execute('SELECT stage FROM local_codex_deliveries').fetchone()[0])
        self.assertEqual([], list_legacy_pending(self.store, self.job))
        self.assertEqual(0, self.store.db.execute("SELECT count(*) FROM message_events WHERE event='Consegnato con ricevuta'").fetchone()[0])

    def test_skipped_slots_are_anchored_and_already_future_due_is_unchanged(self):
        self.assertEqual(1180, self.apply(now=1137)['next_due'])
        other = self.__class__('test_skipped_slots_are_anchored_and_already_future_due_is_unchanged')
        other.setUp()
        try:
            other.store.db.execute('UPDATE timers SET due=1500'); other.store.db.commit()
            self.assertEqual(1500, other.apply(now=1137)['next_due'])
        finally: other.tearDown()

    def test_one_off_is_finished_without_a_future_send(self):
        self.job['repeat'] = False; self.save()
        result = self.apply()
        self.assertEqual('archived_unconfirmed', result['action']); self.assertIsNone(result['next_due'])
        self.assertEqual('finished', self.timer()['phase']); self.assertEqual(1000, self.timer()['due'])
        self.assertEqual(800, self.timer()['last_sent'])

    def test_alias_job_tokens_require_the_entire_popup_set(self):
        alias = str(uuid.uuid4())
        self.store.db.execute('INSERT INTO connector_deliveries VALUES(?,?,?,?,?,?,NULL,NULL)',
                              (alias, 'another-job', 'alias-version', self.job['thread_id'], 'hash', 901))
        self.store.db.commit()
        self.assertEqual({alias, self.token}, {x['token'] for x in list_legacy_pending(self.store, self.job)})
        with self.assertRaises(RecoveryConflict): self.apply()
        self.assertEqual(1000, self.timer()['due'])
        self.assertEqual('future_only', self.apply(expected=[alias, self.token])['action'])

    def test_stale_version_deleted_job_or_changed_target_is_rejected(self):
        self.job['version'] = 'changed'; self.save()
        with self.assertRaises(RecoveryConflict):
            apply_resolution(self.root, self.job['id'], 'previous-version', [self.token], 1120)
        self.job['thread_id'] = str(uuid.uuid4()); self.save()
        with self.assertRaises(RecoveryConflict): self.apply()
        (self.root / 'messages-config.json').write_text('{"jobs":[]}', encoding='utf-8')
        with self.assertRaises(RecoveryConflict): self.apply()
        self.assertEqual('uncertain', self.timer()['phase'])

    def test_new_confirmation_or_cancellation_invalidates_popup(self):
        self.store.db.execute('UPDATE connector_deliveries SET confirmed_at=1100'); self.store.db.commit()
        with self.assertRaises(RecoveryConflict): self.apply()
        self.store.db.execute('UPDATE connector_deliveries SET confirmed_at=NULL')
        self.store.db.execute("UPDATE local_codex_deliveries SET stage='cancelled_before_send'"); self.store.db.commit()
        with self.assertRaises(RecoveryConflict): self.apply()

    def test_an_open_native_alias_blocks_manual_release(self):
        self.store.db.execute('CREATE TABLE native_attempts(job_id TEXT,app TEXT,runtime_id TEXT,target_id TEXT,state TEXT)')
        self.store.db.execute('INSERT INTO native_attempts VALUES(?,?,?,?,?)',
                              ('alias', 'codex', self.job['thread_id'], self.job['thread_id'], 'unknown'))
        self.store.db.commit()
        with self.assertRaises(RecoveryConflict): self.apply()
        self.assertEqual('uncertain', self.timer()['phase'])

    def test_an_overdue_active_alias_cannot_be_released_implicitly(self):
        alias = {**self.job, 'id': str(uuid.uuid4()), 'enabled': True}
        (self.root / 'messages-config.json').write_text(json.dumps({'jobs': [self.job, alias]}), encoding='utf-8')
        self.store.db.execute('INSERT INTO timers VALUES(?,?,?,?,?,?)',
                              (alias['id'], alias['version'], 1000, 'idle', None, 'Alias'))
        self.store.db.commit()
        with self.assertRaises(RecoveryConflict): self.apply()
        self.store.db.execute('UPDATE timers SET due=1500 WHERE id=?', (alias['id'],)); self.store.db.commit()
        self.assertEqual('future_only', self.apply()['action'])
        row = self.store.db.execute('SELECT due,last_sent FROM timers WHERE id=?', (alias['id'],)).fetchone()
        self.assertEqual((1500, None), tuple(row))

    def test_resolution_is_append_only_and_replaying_popup_cannot_move_due(self):
        self.apply()
        with self.assertRaises(RecoveryConflict): self.apply(now=9999)
        self.assertEqual(1180, self.timer()['due'])
        for sql in ('DELETE FROM legacy_delivery_resolutions', 'UPDATE legacy_delivery_resolutions SET at=0'):
            with self.assertRaises(sqlite3.IntegrityError): self.store.db.execute(sql)
            self.store.db.rollback()
        self.assertEqual(1, self.store.db.execute('SELECT count(*) FROM legacy_delivery_resolutions').fetchone()[0])

    def test_readonly_listing_does_not_create_resolution_schema(self):
        list_legacy_pending(self.store, self.job)
        self.assertFalse(self.store.db.execute("SELECT 1 FROM sqlite_master WHERE name='legacy_delivery_resolutions'").fetchone())

    def test_resolution_releases_only_confirmed_target_and_clears_its_retry(self):
        from delivery_journal import Journal
        journal=Journal(self.store)
        binding={'app':'codex','logical_id':self.job['thread_id'],'runtime_id':self.job['thread_id']}
        self.store.fail(self.job,'Vecchio errore',1001)
        self.assertTrue(journal.legacy_pending(self.job,binding))
        self.apply()
        self.assertFalse(journal.legacy_pending(self.job,binding))
        self.assertFalse(self.store.retry_pending(self.job,1120))
        self.assertNotIn('delivery_error',self.store.rows()[0])
        self.assertEqual('future_only',self.store.rows()[0]['legacy_resolution'])
        # A new uncertain reservation is never covered by the old decision.
        self.store.db.execute('INSERT INTO connector_deliveries VALUES(?,?,?,?,?,?,NULL,NULL)',
            (str(uuid.uuid4()),'new-job','new',self.job['thread_id'],'hash',1200))
        self.store.db.commit()
        self.assertTrue(journal.legacy_pending(self.job,binding))

    def test_single_send_can_only_finish_without_claiming_a_receipt(self):
        from schedule_display import schedule_display
        self.job['repeat']=False;self.job['enabled']=True;self.save()
        result=self.apply()
        state=self.store.rows()[0]
        display=schedule_display(self.job,state,{},1120)
        self.assertEqual('archived_unconfirmed',result['action'])
        self.assertEqual('Terminata senza conferma',display['status'])
        self.assertEqual('Completato',display['activation'])
        self.assertEqual(800,state['last_sent'])

    def test_two_gui_requests_cannot_resolve_twice(self):
        barrier = threading.Barrier(2); outcomes = []
        def request():
            barrier.wait()
            try:
                apply_resolution(self.root, self.job['id'], self.job['version'], [self.token], 1120)
                outcomes.append('applied')
            except (RecoveryConflict, OSError, sqlite3.Error): outcomes.append('blocked')
        threads = [threading.Thread(target=request) for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(3); self.assertFalse(thread.is_alive())
        self.assertEqual(['applied', 'blocked'], sorted(outcomes))
        self.assertEqual(1, self.store.db.execute('SELECT count(*) FROM legacy_delivery_resolutions').fetchone()[0])

    def test_claude_uses_exact_logical_and_runtime_ids_and_rejects_unknown_identity(self):
        logical, runtime = str(uuid.uuid4()), str(uuid.uuid4())
        self.store.db.execute('DELETE FROM connector_deliveries')
        self.store.db.execute('CREATE TABLE claude_timer_deliveries(token TEXT,job_id TEXT,version TEXT,identity TEXT,prepared_at REAL,stage TEXT)')
        self.job.update(app='claude', session_id=logical); self.save()
        identity = {'index': str(self.root / ('local_' + logical + '.json')), 'session': runtime}
        self.store.db.execute('INSERT INTO claude_timer_deliveries VALUES(?,?,?,?,?,?)',
                              (self.token, self.job['id'], 'old', json.dumps(identity), 900, 'sending'))
        self.store.db.commit()
        with patch('claude_delivery.locate', return_value=identity):
            self.assertEqual(runtime, list_legacy_pending(self.store, self.job)[0]['target_id'])
            self.assertEqual(runtime, self.apply()['target_id'])
        unknown = str(uuid.uuid4())
        self.store.db.execute('INSERT INTO claude_timer_deliveries VALUES(?,?,?,?,?,?)',
                              (unknown, 'unrelated-job', 'old', '{}', 901, 'prepared')); self.store.db.commit()
        with patch('claude_delivery.locate', return_value=identity), self.assertRaises(RecoveryConflict):
            list_legacy_pending(self.store, self.job)


if __name__ == '__main__': unittest.main()
