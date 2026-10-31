"""Version changes must either remain usable or report a specific blockage."""
from pathlib import Path
from types import SimpleNamespace as Node
from unittest.mock import Mock, patch
import sqlite3
import tempfile
import unittest

from chat_catalog import codex_metadata, codex_inventory
from chat_desktop import inventory, message_editors, step
from schedule_display import schedule_display


class CompatibilityTests(unittest.TestCase):
    def editor(self, name='A future translated placeholder', **changes):
        return Mock(ControlTypeName='EditControl', Name=name, IsEnabled=True,
                    IsOffscreen=False, AriaRole='textbox',
                    AriaProperties='multiline=true;required=false', **changes)

    def test_renamed_empty_busy_composer_then_send_revalidates_without_its_label(self):
        edit=self.editor();edit.GetValuePattern.return_value.Value='Literal'
        stop=Node(ControlTypeName='ButtonControl',Name='Interrompi',IsEnabled=True)
        send=Node(ControlTypeName='ButtonControl',Name='Reindirizza',IsEnabled=True)
        job=dict(app='codex',title='Test',group='Recenti',message='Literal',send_when_active=True)
        with patch('chat_desktop.native_document',return_value=object()), \
             patch('chat_desktop.walk',return_value=[edit,stop,send]), \
             patch('chat_desktop.snapshot',return_value=([edit],'Test')), \
             patch('chat_desktop.entries',return_value=[({**job,'ambiguous':False},object())]), \
             patch('chat_desktop.send_block_reason',return_value=None):
            self.assertEqual(message_editors([edit],'codex'),[edit])
            result=step({'idle_seconds_before_actions':0},job,{'drafted':True},live_guard=lambda:None)
            self.assertIs(result['button'],send)
            self.assertTrue(result['validate_draft']())
            edit.GetValuePattern.return_value.Value='User draft'
            self.assertFalse(result['validate_draft']())
            edit.GetValuePattern.return_value.SetValue.assert_not_called()

    def test_unknown_editor_needs_native_document_and_composer_controls(self):
        edit=self.editor()
        with patch('chat_desktop.native_document',return_value=None),patch('chat_desktop.walk') as walk:
            self.assertEqual(message_editors([edit],'codex'),[]);walk.assert_not_called()
        with patch('chat_desktop.native_document',return_value=object()),patch('chat_desktop.walk',return_value=[]):
            self.assertEqual(message_editors([edit],'codex'),[])
        edit.AriaProperties='multiline=false'
        with patch('chat_desktop.native_document') as document:
            self.assertEqual(message_editors([edit],'codex'),[]);document.assert_not_called()

    def test_two_semantic_composers_are_never_arbitrarily_selected(self):
        editors=[self.editor(),self.editor('Another field')]
        send=Node(ControlTypeName='ButtonControl',Name='Send')
        with patch('chat_desktop.native_document',return_value=object()),patch('chat_desktop.walk',return_value=[send]):
            self.assertEqual(len(message_editors(editors,'codex')),2)
        job=dict(app='codex',title='Test',group='Recenti',message='Literal')
        with patch('chat_desktop.snapshot',return_value=(editors,'Test')), \
             patch('chat_desktop.entries',return_value=[({**job,'ambiguous':False},object())]), \
             patch('chat_desktop.send_block_reason',return_value=None), \
             patch('chat_desktop.message_editors',return_value=editors):
            result=step({'idle_seconds_before_actions':0},job,{},live_guard=lambda:None)
        self.assertIn('2 campi',result['reason']);self.assertIn('non riconosciuta',result['reason'])
        for edit in editors:edit.GetValuePattern.assert_not_called()

    def test_index_tolerates_absent_optional_columns_and_is_read_only(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'state.sqlite'
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE threads(id TEXT,title TEXT,archived INTEGER,updated_at INTEGER,source TEXT)')
                db.executemany('INSERT INTO threads VALUES(?,?,?,?,?)',[('one','Same',0,500,'vscode'),('two','Same',0,501,'vscode'),
                    ('old','Archive',1,502,'vscode'),('child','Internal child',0,503,'{"subagent":{"other":"guardian"}}'),
                    ('empty','',0,504,'vscode')])
            db.close()
            before=path.read_bytes()
            rows=codex_metadata(set(),path,all_chats=True)
            self.assertEqual([r['thread_id'] for r in rows],['one','two'])
            self.assertEqual(rows[0]['last_activity_at'],500)
            self.assertEqual(path.read_bytes(),before)
            self.assertEqual(codex_metadata(set(),path,thread_ids=['two'])[0]['thread_id'],'two')

    def test_broken_index_and_sidebar_report_error_instead_of_empty_success(self):
        with patch('chat_catalog.codex_metadata',side_effect=sqlite3.OperationalError('schema changed')):
            with self.assertRaisesRegex(ValueError,'Indice Codex non leggibile'):
                codex_inventory([])

    def test_index_lists_ids_despite_renamed_sidebar_controls_or_hidden_window(self):
        records=[dict(thread_id='one',title='Renamed',project='P',last_activity_at=500)]
        with patch('chat_catalog.codex_metadata',return_value=records), \
             patch('chat_desktop.snapshot',side_effect=RuntimeError('not available')):
            result=inventory({},'codex')
        self.assertEqual(result['chats'][0]['session_id'],'one')
        self.assertIn('non disponibile',result['metadata_warning'])
        with patch('chat_catalog.codex_metadata',return_value=records):
            rows,warning=codex_inventory([])
        self.assertEqual(len(rows),1);self.assertIn('non riconosciuta',warning)

    def test_native_chatgpt_rows_without_codex_id_are_not_offered_as_codex_recipients(self):
        native=[dict(app='codex',title='ChatGPT only',group='Recenti')]
        with patch('chat_catalog.codex_metadata',return_value=[]):
            rows,_=codex_inventory(native)
        self.assertEqual(rows,[])

    def test_unrecognized_composer_is_error_even_with_pending_reservation_and_busy_pc(self):
        job=dict(version='v1',enabled=True,activated_at=0,interval_seconds=60,app='codex')
        state=dict(version='v1',due=60,phase='uncertain',local_stage='reserved',
                   detail='Invio bloccato: interfaccia Codex non riconosciuta (0 campi messaggio compatibili).')
        display=schedule_display(job,state,{},100,waiting_reason='PC in uso')
        self.assertEqual(display['status'],'Invio bloccato')
        self.assertIn('Aggiorna',display['next'])


if __name__=='__main__':unittest.main()
