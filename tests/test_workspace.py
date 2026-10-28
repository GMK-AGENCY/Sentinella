import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from message_engine import TimerStore


class WorkspaceTests(unittest.TestCase):
    def test_send_started_in_history_is_not_a_current_receipt_failure(self):
        from workspace_ui import history_event_label
        row={'event':'Da verificare','detail':'Invio iniziato: verifica necessaria se il processo si interrompe'}
        self.assertEqual(history_event_label(row),'Invio avviato')
        self.assertEqual(history_event_label({'event':'Da verificare','detail':'Ricevuta assente'}),'Ricezione non confermata')
        self.assertEqual(history_event_label({'event':'Consegnato con ricevuta'}),'Consegnato con ricevuta')
    def test_history_filters_before_limit_and_preserves_duplicate_titles(self):
        with tempfile.TemporaryDirectory() as folder:
            store=TimerStore(Path(folder)/'db.sqlite3')
            one={'id':'one','title':'Same title'};two={'id':'two','title':'Same title'}
            store.event(one,'Da verificare','old event',at=100)
            for i in range(210):store.event(two,'In attesa','other job',at=200+i)
            rows=store.history('one',since=50,outcome='attention')
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['job_id'],'one')
            self.assertEqual(store.history('one',since=101),[])
            self.assertEqual(store.history('two',outcome='confirmed'),[])
            store.event(two,'In attesa','duplicate timestamp',at=400)
            rows=store.history('two',outcome='waiting')
            self.assertEqual(len({r['seq'] for r in rows}),len(rows))
            store.db.close()


if __name__=='__main__':unittest.main()
