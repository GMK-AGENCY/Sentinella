"""Two workers may observe one draft, but only one may claim its Send."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from claude_delivery import Delivery
from message_engine import TimerStore


class DeliveryConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.history=self.root/'history.jsonl';self.history.write_text('')
        self.identity={'session':'session','history':str(self.history),'index':'index'}
        self.job=dict(id='job',version='v1',title='Fixture',group='Project',app='claude',
                      message='Literal message',interval_seconds=60,enabled=True)
        self.first=TimerStore(self.root/'state.sqlite3');self.addCleanup(self.first.db.close)
        self.second=TimerStore(self.root/'state.sqlite3');self.addCleanup(self.second.db.close)
        self.a=Delivery(self.first,self.root);self.b=Delivery(self.second,self.root)
        p=patch('claude_delivery.locate',return_value=self.identity);p.start();self.addCleanup(p.stop)
        self.a.prepare(self.job)

    def test_stale_prepared_observation_cannot_claim_an_already_started_send(self):
        stale=self.b.current(self.job)
        self.a.begin_send(self.job)
        original=self.a.current(self.job)['send_started']
        with patch.object(self.b,'current',return_value=stale):
            with self.assertRaises(ValueError):self.b.begin_send(self.job)
        self.assertEqual(self.a.current(self.job)['send_started'],original)

    def test_another_worker_cannot_cancel_the_owners_claim(self):
        self.a.begin_send(self.job)
        self.b.cancel_before_send(self.job)
        self.assertEqual(self.a.current(self.job)['stage'],'sending')
        self.assertIsNotNone(self.a.current(self.job)['send_started'])

    def test_owner_can_cancel_before_invoke_and_one_later_claim_is_possible(self):
        self.a.begin_send(self.job)
        self.a.cancel_before_send(self.job)
        self.assertEqual(self.a.current(self.job)['stage'],'prepared')
        self.b.begin_send(self.job)
        self.a.cancel_before_send(self.job)
        self.assertEqual(self.b.current(self.job)['stage'],'sending')


if __name__=='__main__':unittest.main()
