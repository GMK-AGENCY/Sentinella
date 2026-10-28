import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from message_library import MessageLibraryPanel, MessageLibraryStore, MessageLibraryChooser
from default_message_templates import LEGACY_TEMPLATE_TEXTS_BY_ID, starter_library
from message_templates import AUTONOMOUS_CONTINUATION


class AutonomousStarterTests(unittest.TestCase):
    def test_all_twelve_preserve_historical_ids_titles_and_collection_shape(self):
        data=starter_library();items=data['items']
        self.assertEqual(len(data['collections']),4);self.assertEqual(len(items),12)
        self.assertEqual({item['id'] for item in items},set(LEGACY_TEMPLATE_TEXTS_BY_ID))
        for item in items:
            with self.subTest(title=item['title']):
                old=LEGACY_TEMPLATE_TEXTS_BY_ID[item['id']]
                self.assertEqual(item['title'],old['title'])
                self.assertNotEqual(item['text'],old['text'])
                self.assertEqual(set(old),{'title','text'})
                self.assertEqual(item['updated_at'],0)
                self.assertEqual(len([row for row in items if row['collection_id']==item['collection_id']]),3)

    def test_every_message_directs_completion_without_waiting_for_absent_user(self):
        for item in starter_library()['items']:
            with self.subTest(title=item['title']):
                text=item['text'];folded=text.casefold()
                self.assertIn(AUTONOMOUS_CONTINUATION,text)
                self.assertIn('utente è assente',folded)
                self.assertIn('fino a completare il lavoro già autorizzato',folded)
                self.assertIn('attività indipendenti già autorizzate',folded)
                self.assertIn('informazione o un permesso indispensabile',folded)
                self.assertIn('annota il punto',folded)
                self.assertIn('non aggirarli',folded)
                self.assertIn('non attribuirti nuove autorizzazioni',folded)
                self.assertIn('senza ampliare il perimetro',folded)
                self.assertLessEqual(len(text),12000)
                for phrase in ('prima di procedere','proponi','attendi una risposta','aspetta una risposta','domani'):
                    self.assertNotIn(phrase,folded)


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = MessageLibraryStore(self.root / "message-library.json")
        self.store.path.write_text(json.dumps({'schema_version':1,'items':[]}),encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    def test_new_library_has_four_collections_and_twelve_stable_templates_without_files(self):
        self.store.path.unlink()
        (self.root / "messages-config.json").write_text('{"jobs": [{"message": "private"}]}')
        first=self.store.list()
        self.assertEqual(len(first),12)
        self.assertEqual(len(self.store.collections()),4)
        self.assertEqual({row['id'] for row in first},{row['id'] for row in self.store.list()})
        self.assertTrue(all(len(self.store.list(collection_id=row['id']))==3 for row in self.store.collections()))
        self.assertTrue(all(row['text']!='private' for row in first))
        self.assertFalse(self.store.path.exists())
        self.assertFalse(self.store.backup.exists())

    def test_existing_empty_library_is_not_seeded(self):
        before=self.store.path.read_bytes()
        self.assertEqual(self.store.list(),[])
        self.assertEqual(self.store.path.read_bytes(),before)
        self.assertFalse(self.store.backup.exists())

    def test_seed_persists_once_and_never_reappears_after_delete(self):
        self.store.path.unlink()
        defaults=self.store.list();removed=defaults[0]
        self.store.delete(removed['id'])
        self.assertEqual(len(self.store.list()),11)
        self.store.save('Personale','Testo utente')
        self.assertEqual(len(MessageLibraryStore(self.store.path).list()),12)
        self.assertIsNone(self.store.get(removed['id']))

    def test_legacy_items_keep_identity_and_literal_text_and_are_not_rewritten_on_read(self):
        import uuid
        item={'id':str(uuid.uuid4()),'title':'Vecchio','text':'È letterale\n  spazi\n','updated_at':1}
        self.store.path.write_text(json.dumps({'schema_version':1,'items':[item]}),encoding='utf-8')
        before=self.store.path.read_bytes()
        read=self.store.list()[0]
        self.assertEqual(read['id'],item['id']);self.assertEqual(read['text'],item['text'])
        self.assertEqual(self.store.path.read_bytes(),before)
        self.assertEqual(self.store.collections()[0]['name'],'Personali')
        self.store.save('Modificato',item['text'],item['id'])
        self.assertEqual(json.loads(self.store.backup.read_text(encoding='utf-8'))['items'],[item])

    def test_collection_crud_move_search_and_nonempty_delete_guard(self):
        first=self.store.save_collection('Lavoro');second=self.store.save_collection('Qualità')
        item=self.store.save('Saluto','Testo letterale',collection_id=first['id'])
        with self.assertRaises(ValueError):self.store.delete_collection(first['id'])
        self.store.save_collection('Clienti',first['id'])
        self.assertEqual(self.store.list('clienti')[0]['id'],item['id'])
        moved=self.store.save(item['title'],item['text'],item['id'],second['id'])
        self.assertEqual(moved['id'],item['id']);self.assertEqual(moved['text'],item['text'])
        self.assertEqual(self.store.list(collection_id=first['id']),[])
        self.store.delete_collection(first['id'])
        self.assertNotIn(first['id'],{row['id'] for row in self.store.collections()})
        with self.assertRaises(ValueError):self.store.save_collection(' qualità ')
        with self.assertRaises(ValueError):self.store.save('Titolo','Testo',collection_id='missing')

    def test_crud_preserves_uuid_literal_unicode_text_and_other_items(self):
        text = "È già pronto?\n  Spazi e LF preservati.\n"
        one = self.store.save(" Primo ", text)
        two = self.store.save("Secondo", "Altro messaggio")
        changed = self.store.save("Titolo aggiornato", text, one["id"])
        self.assertEqual(changed["id"], one["id"])
        self.assertEqual(self.store.get(one["id"])["text"], text)
        self.assertEqual(self.store.list("aggiornato")[0]["id"], one["id"])
        self.assertEqual(self.store.list("spazi")[0]["text"], text)
        self.store.delete(one["id"])
        self.assertEqual([row["id"] for row in self.store.list()], [two["id"]])
        self.assertIsNone(self.store.get(one["id"]))

    def test_failed_primary_replace_keeps_original_and_valid_backup(self):
        one = self.store.save("Saved", "Original")
        previous = self.store.path.read_bytes()
        from message_library import os
        replace = os.replace
        def guarded(source, destination):
            if destination == self.store.path:
                raise OSError("disk failure")
            return replace(source, destination)
        with patch("message_library.os.replace", side_effect=guarded):
            with self.assertRaises(OSError):
                self.store.save("Changed", "New", one["id"])
        self.assertEqual(self.store.path.read_bytes(), previous)
        self.assertEqual(json.loads(self.store.backup.read_text())["items"][0]["text"], "Original")
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_corrupt_primary_recovers_backup_without_overwriting_it_with_corruption(self):
        one = self.store.save("First", "Prior value")
        self.store.save("Second", "Other value")
        self.store.path.write_text("invalid json")
        self.assertEqual(self.store.list()[0]["id"], one["id"])
        self.assertTrue(self.store.recovery_notice)
        self.store.save("Restored", "Recovered", one["id"])
        self.assertEqual(self.store.get(one["id"])["text"], "Recovered")
        self.assertEqual(json.loads(self.store.backup.read_text())["items"][0]["text"], "Prior value")

    def test_corrupt_both_fail_without_replacing_user_files(self):
        for path in (self.store.path, self.store.backup):
            path.write_text("broken")
        with self.assertRaises(ValueError):
            self.store.save("New", "Text")
        self.assertEqual(self.store.path.read_text(), "broken")
        self.assertEqual(self.store.backup.read_text(), "broken")

    def test_validation_and_unknown_id_do_not_change_saved_texts(self):
        one = self.store.save("Saved", "Literal")
        for title, text in ((" ", "text"), ("a" * 161, "text"), ("title", "\n"), ("title", "b" * 12001)):
            with self.assertRaises(ValueError):
                self.store.save(title, text)
        with self.assertRaises(ValueError):
            self.store.save("Missing", "Text", "missing")
        self.assertEqual(self.store.get(one["id"])["text"], "Literal")

    def test_use_only_calls_text_callback_and_never_saves(self):
        view = SimpleNamespace(text=Mock(), status=Mock(), on_use=Mock(), store=Mock(), may_leave=Mock(return_value=True))
        view.text.get.return_value = "Testo letterale\n"
        MessageLibraryPanel.use(view)
        view.on_use.assert_called_once_with("Testo letterale\n")
        view.store.save.assert_not_called()
        self.assertFalse((self.root / "messages.sqlite3").exists())

    def test_cancelled_composer_reuse_does_not_claim_text_copied(self):
        view = SimpleNamespace(text=Mock(), status=Mock(), on_use=Mock(return_value=False), may_leave=Mock(return_value=True))
        view.text.get.return_value = "Literal"
        MessageLibraryPanel.use(view)
        self.assertIn("annullato", view.status.set.call_args.args[0])

    def test_new_from_text_stages_without_saving_and_respects_dirty_cancel(self):
        view = SimpleNamespace(store=self.store, status=Mock(), _may_leave=Mock(return_value=False),
                               _load=Mock(), text=Mock(), tree=Mock())
        view.tree.selection.return_value = ()
        self.assertFalse(MessageLibraryPanel.new_from_text(view, "Literal\n"))
        view._load.assert_not_called()
        view._may_leave.return_value = True
        self.assertTrue(MessageLibraryPanel.new_from_text(view, "Literal\n"))
        view._load.assert_called_once_with()
        view.text.insert.assert_called_once_with("1.0", "Literal\n")
        self.assertEqual(self.store.list(),[])

    def test_default_path_uses_runtime_profile(self):
        with patch("message_library.data_root", return_value=self.root):
            store = MessageLibraryStore()
        self.assertEqual(store.path, self.root / "message-library.json")

    def test_save_during_selection_keeps_requested_row_selected(self):
        view = SimpleNamespace(tree=Mock(), rendering=False, selected_id="first", _may_leave=Mock(return_value=True),
                               items=[{"id": "second", "title": "Second", "text": "Literal"}], _load=Mock())
        view.tree.selection.return_value = ("second",)
        MessageLibraryPanel.select(view)
        view._load.assert_called_once_with(view.items[0])
        view.tree.selection_set.assert_called_once_with("second")

    def test_cancelled_dirty_guard_preserves_editor(self):
        view = SimpleNamespace(is_dirty=lambda: True, save=Mock(), discard_changes=Mock())
        view.may_leave=lambda:MessageLibraryPanel.may_leave(view)
        with patch("unsaved_changes._choose_unsaved", return_value=None):
            self.assertFalse(MessageLibraryPanel._may_leave(view))
        view.save.assert_not_called()
        with patch("unsaved_changes._choose_unsaved", return_value="save"):
            view.save.return_value = False
            self.assertFalse(MessageLibraryPanel._may_leave(view))


class CollectionInterfaceTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from tkinter import ttk
        self.temp=tempfile.TemporaryDirectory();self.folder=Path(self.temp.name)
        self.profile_patch=patch('message_library.data_root',return_value=self.folder);self.profile_patch.start()
        self.root=tk.Tk();self.root.withdraw();self.root.attributes('-alpha',0.0);self.root.attributes('-disabled',True)
        style=ttk.Style(self.root);style.theme_use('clam');style.configure('TButton',font=('Segoe UI',10),padding=(13,9))
        style.configure('TLabel',font=('Segoe UI',10));style.configure('Section.TLabel',font=('Segoe UI',12,'bold'))
        style.configure('Treeview',font=('Segoe UI',10),rowheight=36);style.configure('Treeview.Heading',font=('Segoe UI',10,'bold'),padding=(8,10))
        sidebar=ttk.Frame(self.root,width=192);sidebar.pack(side='left',fill='y')
        main=ttk.Frame(self.root,padding=(24,22));main.pack(side='left',fill='both',expand=True)
        self.panel=MessageLibraryPanel(main,Mock(return_value=True));self.panel.pack(fill='both',expand=True)
        self.root.deiconify()

    def tearDown(self):
        self.root.destroy();self.profile_patch.stop();self.temp.cleanup()

    def test_collection_filter_dirty_move_and_literal_reuse(self):
        panel=self.panel
        self.assertEqual(len(panel.items),12)
        self.assertFalse(panel.store.path.exists())
        panel.collection_filter.set('Qualità')
        self.assertEqual(len(panel.tree.get_children()),3)
        key=panel.tree.get_children()[0];panel.tree.selection_set(key);panel.select()
        literal=panel.text.get('1.0','end-1c')
        self.assertEqual(panel.collection_value.get(),'Qualità')
        panel.collection_value.set('Riepiloghi')
        with patch('unsaved_changes._choose_unsaved',return_value=None):panel.new()
        self.assertEqual(panel.selected_id,key)
        self.assertEqual(panel.text.get('1.0','end-1c'),literal)
        self.assertTrue(panel.save())
        self.assertEqual(panel.store.get(key)['collection_id'],panel.collection_options['Riepiloghi'])
        self.assertEqual(len(panel.store.list()),12)
        panel.use();panel.on_use.assert_called_once_with(literal)

    def test_collection_controls_fit_at_both_workspace_sizes_and_fields_align(self):
        from tkinter import ttk
        def widgets(parent):
            for child in parent.winfo_children():
                yield child
                yield from widgets(child)
        for size in ('1040x790','1280x850'):
            self.root.geometry(size);self.root.update();self.root.update_idletasks()
            for button in (child for child in widgets(self.panel) if isinstance(child,ttk.Button)):
                self.assertGreaterEqual(button.winfo_width(),button.winfo_reqwidth(),button.cget('text'))
                self.assertLessEqual(button.winfo_rootx()+button.winfo_width(),self.root.winfo_rootx()+self.root.winfo_width())
                self.assertLessEqual(button.winfo_rooty()+button.winfo_height(),self.root.winfo_rooty()+self.root.winfo_height())
            self.assertEqual(self.panel.model_collection.winfo_rooty(),self.panel.title_entry.winfo_rooty())
            use=next(child for child in widgets(self.panel) if isinstance(child,ttk.Button) and child.cget('text')=='Usa nella programmazione')
            self.assertLess(use.winfo_rootx(),self.panel.save_button.winfo_rootx())

    def test_message_actions_footer_placeholder_and_collection_icons(self):
        from tkinter import ttk
        from ui_components import PlaceholderEntry
        def widgets(parent):
            for child in parent.winfo_children():
                yield child
                yield from widgets(child)
        panel=self.panel
        self.assertEqual(panel.status.get(),'')
        self.assertTrue(panel.rename_collection_button.instate(['disabled']))
        self.assertTrue(panel.delete_collection_button.instate(['disabled']))
        self.assertEqual(panel.rename_collection_button.cget('text'),'')
        self.assertEqual(panel.delete_collection_button.cget('text'),'')
        self.assertTrue(panel.rename_collection_button.cget('image'))
        self.assertTrue(panel.delete_collection_button.cget('image'))
        self.assertEqual([tip.text for tip in panel.collection_tooltips],['Rinomina raccolta','Elimina raccolta'])
        panel.collection_filter.set('Qualità')
        self.assertFalse(panel.rename_collection_button.instate(['disabled']))
        self.assertFalse(panel.delete_collection_button.instate(['disabled']))
        panel.collection_filter.set('Tutte le raccolte')
        self.assertTrue(panel.rename_collection_button.instate(['disabled']))
        entries=[item for item in widgets(panel) if isinstance(item,PlaceholderEntry)]
        self.assertEqual(len(entries),1);self.assertEqual(panel.query.get(),'')
        self.root.geometry('1040x790');self.root.update();self.root.update_idletasks()
        buttons={button.cget('text'):button for button in widgets(panel) if isinstance(button,ttk.Button)}
        use=buttons['Usa nella programmazione'];delete=buttons['Elimina messaggio'];save=buttons['Salva messaggio']
        self.assertIs(use.master,delete.master);self.assertIs(delete.master,save.master)
        self.assertLess(use.winfo_rootx(),delete.winfo_rootx());self.assertLess(delete.winfo_rootx(),save.winfo_rootx())
        self.assertEqual(use.winfo_rooty(),save.winfo_rooty())
        self.assertLess(panel.text.winfo_rooty(),save.winfo_rooty())
        self.assertLess(buttons['+ Nuovo messaggio'].winfo_rooty(),panel.title_entry.winfo_rooty())
        self.assertFalse(any(word in str(widget.cget('text')).casefold() for widget in widgets(panel) if isinstance(widget,ttk.Label) for word in ('modello','modelli')))

    def test_chooser_filters_collection_search_and_preserves_literal_callback(self):
        captured=[];chooser=MessageLibraryChooser(self.panel,lambda text:captured.append(text))
        chooser.withdraw();chooser.attributes('-alpha',0.0);chooser.attributes('-disabled',True);chooser.deiconify()
        for size in ('620x520','760x620'):
            chooser.geometry(size);self.root.update();self.root.update_idletasks()
            self.assertGreaterEqual(chooser.preview.winfo_height(),80)
            self.assertLessEqual(chooser.use_button.winfo_rooty()+chooser.use_button.winfo_height(),chooser.winfo_rooty()+chooser.winfo_height())
        chooser.collection_filter.set('Pianificazione')
        self.assertEqual(len(chooser.tree.get_children()),3)
        chooser.query.set('prossimo passo')
        self.assertGreaterEqual(len(chooser.tree.get_children()),1)
        chooser.tree.selection_set(chooser.tree.get_children()[0]);chooser.select()
        literal=chooser.preview.get('1.0','end-1c')
        chooser.use();self.assertEqual(captured,[literal])
        self.assertFalse(self.panel.store.path.exists())

    def test_chooser_long_preview_border_footer_and_autoscroll_stay_contained(self):
        long_text=('Riga di anteprima con spazi e caratteri italiani: è già pronta.\n'*100)+'ParolaLunga'*300
        item=self.panel.store.save('Anteprima lunga per resize',long_text)
        chooser=MessageLibraryChooser(self.panel,lambda _text:True)
        chooser.withdraw();chooser.attributes('-alpha',0.0);chooser.attributes('-disabled',True);chooser.deiconify()
        chooser.query.set('Anteprima lunga per resize');chooser.tree.selection_set(item['id']);chooser.select()
        for size in ('620x520','760x620','620x520'):
            chooser.geometry(size);self.root.update();self.root.update_idletasks()
            frame=chooser.preview_frame;text=chooser.preview;scroll=chooser.preview_scroll
            fx,fy=frame.winfo_rootx(),frame.winfo_rooty();right=fx+frame.winfo_width();bottom=fy+frame.winfo_height()
            self.assertEqual(int(frame.cget('highlightthickness')),1)
            self.assertEqual(int(text.cget('highlightthickness')),0)
            self.assertGreaterEqual(text.winfo_rootx(),fx+1);self.assertGreaterEqual(text.winfo_rooty(),fy+1)
            self.assertLessEqual(text.winfo_rootx()+text.winfo_width(),right-1)
            self.assertLessEqual(text.winfo_rooty()+text.winfo_height(),bottom-1)
            self.assertLessEqual(scroll.winfo_rootx()+scroll.winfo_width(),right-1)
            self.assertLessEqual(scroll.winfo_rooty()+scroll.winfo_height(),bottom-1)
            self.assertLess(bottom,chooser.cancel_button.winfo_rooty())
            self.assertLess(chooser.cancel_button.winfo_rootx(),chooser.use_button.winfo_rootx())
            self.assertLessEqual(chooser.use_button.winfo_rootx()+chooser.use_button.winfo_width(),chooser.winfo_rootx()+chooser.winfo_width()-16)
            self.assertLessEqual(chooser.use_button.winfo_rooty()+chooser.use_button.winfo_height(),chooser.winfo_rooty()+chooser.winfo_height()-16)
            self.assertGreaterEqual(chooser.use_button.winfo_width(),chooser.use_button.winfo_reqwidth())
            self.assertEqual(text.xview(),(0.0,1.0))
            self.assertEqual(scroll.winfo_manager(),'grid')
            text.see('end');self.root.update_idletasks()
            self.assertEqual(text.get('1.0','end-1c'),long_text)
            self.assertAlmostEqual(text.yview()[1],1.0)
        chooser.query.set('__nessun_messaggio__');self.root.update();self.root.update_idletasks()
        self.assertFalse(chooser.preview_scroll.winfo_manager())
        chooser.destroy()

    def test_new_and_rename_collection_dialogs_keep_unrelated_editor_model(self):
        import tkinter as tk
        from tkinter import ttk
        original_top=tk.Toplevel;opened=[]
        def hidden_top(*args,**kwargs):
            window=original_top(*args,**kwargs);window.withdraw();window.attributes('-alpha',0.0);window.attributes('-disabled',True);opened.append(window);return window
        def descendants(parent):
            for child in parent.winfo_children():
                yield child
                yield from descendants(child)
        self.panel.collection_filter.set('Avanzamento')
        key=self.panel.tree.get_children()[0];self.panel.tree.selection_set(key);self.panel.select()
        before=(self.panel.selected_id,self.panel.text.get('1.0','end-1c'),self.panel.collection_value.get())
        self.panel.collection_filter.set('Riepiloghi')
        with patch('message_library.tk.Toplevel',side_effect=hidden_top):self.panel.collection_dialog(rename=True)
        window=opened[-1]
        entry=next(child for child in descendants(window) if isinstance(child,ttk.Entry))
        entry.delete(0,'end');entry.insert(0,'Riepiloghi mensili')
        next(child for child in descendants(window) if isinstance(child,ttk.Button) and child.cget('text')=='Salva raccolta').invoke()
        self.assertEqual((self.panel.selected_id,self.panel.text.get('1.0','end-1c'),self.panel.collection_value.get()),before)
        self.assertIn('Riepiloghi mensili',self.panel.collection_options)
        with patch('message_library.tk.Toplevel',side_effect=hidden_top):self.panel.collection_dialog()
        window=opened[-1];entry=next(child for child in descendants(window) if isinstance(child,ttk.Entry))
        entry.insert(0,'Cliente esempio')
        next(child for child in descendants(window) if isinstance(child,ttk.Button) and child.cget('text')=='Crea raccolta').invoke()
        self.assertEqual(self.panel.collection_filter.get(),'Cliente esempio')
        self.assertEqual(self.panel.tree.get_children(),())
        self.assertIsNone(self.panel.selected_id)
        with patch('message_library.messagebox.askyesno',return_value=True):self.panel.remove_collection()
        self.assertNotIn('Cliente esempio',self.panel.collection_options)


if __name__ == "__main__":
    unittest.main()
