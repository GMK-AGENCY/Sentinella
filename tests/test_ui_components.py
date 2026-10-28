"""Stock Tk controls and immutable source/frozen assets; no desktop actions."""
from pathlib import Path
import struct
import tempfile
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

import app_icon
from ui_components import AutoScrollbar, PlaceholderEntry, TreeRowActions, photo_icon


class ProcessIdentityTests(unittest.TestCase):
    def test_stable_windows_process_identity_before_any_tk_in_source_and_frozen(self):
        for frozen in (False,True):
            identify=Mock(return_value=0)
            shell=SimpleNamespace(shell32=SimpleNamespace(SetCurrentProcessExplicitAppUserModelID=identify))
            with patch.object(app_icon.sys,'platform','win32'),patch.object(app_icon.sys,'frozen',frozen,create=True), \
                    patch.object(app_icon.ctypes,'windll',shell,create=True),patch.object(app_icon.tk,'Tk',side_effect=AssertionError('Must run before Tk')):
                self.assertTrue(app_icon.set_process_identity())
            identify.assert_called_once_with('Sentinella.Desktop')
            self.assertEqual(identify.argtypes,[app_icon.ctypes.c_wchar_p])
            self.assertIs(identify.restype,app_icon.ctypes.c_long)

    def test_unsupported_platform_does_not_call_windows_api(self):
        shell=Mock()
        with patch.object(app_icon.sys,'platform','linux'),patch.object(app_icon.ctypes,'windll',shell,create=True):
            self.assertFalse(app_icon.set_process_identity())
        self.assertEqual(shell.mock_calls,[])

    def test_hresult_and_native_failures_do_not_block_startup(self):
        identify=Mock()
        shell=SimpleNamespace(shell32=SimpleNamespace(SetCurrentProcessExplicitAppUserModelID=identify))
        with patch.object(app_icon.sys,'platform','win32'),patch.object(app_icon.ctypes,'windll',shell,create=True):
            for code in (1,0x80004005,-2147467259):
                identify.return_value=code
                self.assertFalse(app_icon.set_process_identity())
            for error in (OSError('API unavailable'),AttributeError('missing API'),TypeError('invalid API'),ValueError('invalid value'),RuntimeError('unavailable')):
                identify.side_effect=error
                self.assertFalse(app_icon.set_process_identity())

    def test_windows_icons_apply_native_current_and_default_after_photo_fallback(self):
        window=Mock();photo=Mock();photo.subsample.side_effect=lambda factor:f'photo-{factor}'
        ico,png=app_icon.icon_paths();before={path:path.read_bytes() for path in (ico,png)}
        with patch.object(app_icon.sys,'platform','win32'),patch.object(app_icon.tk,'PhotoImage',return_value=photo):
            self.assertTrue(app_icon.set_window_icon(window))
        self.assertEqual(window.method_calls,[call.iconphoto(True,'photo-32','photo-16','photo-8','photo-4','photo-2',photo),call.iconbitmap(default=str(ico)),call.iconbitmap(str(ico))])
        self.assertIs(window._sentinella_icon_image,photo)
        self.assertEqual(len(window._sentinella_icon_images),6)
        self.assertEqual({path:path.read_bytes() for path in (ico,png)},before)

    def test_each_icon_format_can_fall_back_and_both_failures_are_reported(self):
        with patch.object(app_icon.sys,'platform','win32'):
            window=Mock();window.iconbitmap.side_effect=tk.TclError('ICO unavailable')
            with patch.object(app_icon.tk,'PhotoImage',return_value=Mock()):
                self.assertTrue(app_icon.set_window_icon(window))
            window=Mock()
            with patch.object(app_icon.tk,'PhotoImage',side_effect=tk.TclError('PNG unavailable')):
                self.assertTrue(app_icon.set_window_icon(window))
                window.iconbitmap.assert_any_call(str(app_icon.icon_paths()[0]))
                window.iconbitmap.side_effect=tk.TclError('ICO unavailable')
                self.assertFalse(app_icon.set_window_icon(window))


class UIComponentsTests(unittest.TestCase):
    def action_tree(self, count=40):
        host=ttk.Frame(self.root);host.pack(fill='both',expand=True)
        ttk.Style(self.root).configure('ActionsTest.Treeview',rowheight=36)
        tree=ttk.Treeview(host,columns=('title','last','toggle','delete'),show='headings',
                          style='ActionsTest.Treeview',height=5)
        for column,width in (('title',190),('last',100),('toggle',106),('delete',40)):
            tree.column(column,width=width,minwidth=width,stretch=column=='title')
        bar=AutoScrollbar(host,command=tree.yview);bar.pack(side='right',fill='y')
        tree.pack(side='left',fill='both',expand=True)
        observed=[]
        def yscroll(first,last):
            observed.append((first,last));bar.set(first,last)
        tree.configure(yscrollcommand=yscroll)
        paused={f'job-{i}':bool(i%2) for i in range(count)}
        for iid in paused:tree.insert('','end',iid=iid,values=(iid,'12:30','',''))
        toggle=Mock(side_effect=lambda key:paused.__setitem__(key,not paused[key]))
        delete=Mock()
        actions=TreeRowActions(tree,on_toggle=toggle,on_delete=delete,is_paused=paused.get)
        self.layout()
        return tree,actions,paused,toggle,delete,observed

    def test_canonical_action_icons_are_native_and_font_independent(self):
        for name in ('pause','play','trash'):
            icon=photo_icon(self.root,name,size=20,color='#17243b')
            self.assertEqual((icon.width(),icon.height()),(20,20))
            self.assertTrue(icon.transparency_get(0,0))
        self.assertFalse(photo_icon(self.root,'pause',20).transparency_get(5,8))
        with self.assertRaises(ValueError):photo_icon(self.root,'unknown')
        with self.assertRaises(ValueError):photo_icon(self.root,'play',size=4)

    def test_row_actions_only_allocate_visible_rows_and_preserve_scroll_callbacks(self):
        tree,actions,paused,toggle,delete,observed=self.action_tree(1000)
        self.assertGreater(len(actions.buttons),0)
        self.assertLessEqual(len(actions.buttons),self.root.winfo_height()//36+2)
        before=set(actions.buttons)
        self.assertGreater(len(observed),0)
        tree.yview_moveto(1);self.layout()
        self.assertIn('job-999',actions.buttons)
        self.assertNotEqual(set(actions.buttons),before)
        self.assertTrue(all(tree.bbox(key) for key in actions.buttons))
        self.assertLessEqual(len(actions.buttons),self.root.winfo_height()//36+2)
        self.assertIsNone(actions._pending)
        previous=tree.yview()
        self.assertEqual(actions._wheel(SimpleNamespace(delta=120,state=0)),'break')
        self.layout()
        self.assertLess(tree.yview()[0],previous[0])
        toggle.assert_not_called();delete.assert_not_called()

    def test_row_buttons_use_stable_id_fresh_state_and_canonical_images(self):
        tree,actions,paused,toggle,delete,_=self.action_tree()
        tree.selection_set('job-3');self.layout()
        button=actions.buttons['job-0']['toggle']
        self.assertEqual(button.cget('text'),'Pausa')
        button.invoke();self.layout()
        toggle.assert_called_once_with('job-0')
        self.assertEqual(button.cget('text'),'Riprendi')
        self.assertEqual(str(button.cget('image')[0]),str(actions.icons['play']))
        self.assertEqual(tree.selection(),('job-3',))
        self.assertEqual(actions._invoke_key('job-0','delete'),'break')
        delete.assert_called_once_with('job-0')
        self.assertEqual(str(actions.buttons['job-0']['delete'].cget('compound')),'none')

    def test_stale_position_removed_job_and_deleted_row_cannot_invoke_actions(self):
        tree,actions,paused,toggle,delete,_=self.action_tree()
        button=actions.buttons['job-0']['delete']
        tree.move('job-0','','end')
        button.invoke()  # Event arrived before the coalesced layout refresh.
        delete.assert_not_called();self.layout()
        current=next(iter(actions.buttons))
        paused.pop(current)
        actions.buttons[current]['toggle'].invoke()
        toggle.assert_not_called();self.layout()
        self.assertNotIn(current,actions.buttons)
        current=next(iter(actions.buttons))
        button=actions.buttons[current]['delete']
        tree.delete(current);button.invoke()
        delete.assert_not_called();self.layout()

    def test_actions_track_resize_horizontal_scroll_and_unmap_without_polling(self):
        tree,actions,paused,toggle,delete,_=self.action_tree()
        tree.column('title',width=800,minwidth=800,stretch=False)
        self.layout()
        self.assertTrue(all(row['toggle'].winfo_manager()=='' for row in actions.buttons.values()))
        tree.xview_moveto(1);self.layout()
        visible=[row for row in actions.buttons.values() if row['delete'].winfo_manager()=='place']
        self.assertTrue(visible)
        for row in visible:
            self.assertLess(row['toggle'].winfo_x(),row['delete'].winfo_x())
            self.assertLessEqual(row['delete'].winfo_x()+row['delete'].winfo_width(),tree.winfo_width())
        self.root.geometry('620x420');self.layout()
        self.assertIsNone(actions._pending)
        self.root.withdraw();self.root.update()
        self.assertEqual(actions.buttons,{})
        self.layout();self.assertTrue(actions.buttons)
        toggle.assert_not_called();delete.assert_not_called()

    def test_action_destroy_restores_original_scroll_commands_and_keeps_tree_usable(self):
        tree,actions,paused,toggle,delete,observed=self.action_tree()
        original=dict(actions._scroll_originals)
        actions.destroy();self.layout()
        self.assertEqual(actions.buttons,{})
        self.assertEqual(str(tree.cget('yscrollcommand')),original['y'])
        count=len(observed);tree.yview_moveto(1);self.layout()
        self.assertGreater(len(observed),count)
        self.assertIsNone(actions._pending)
        actions.destroy()

    def test_placeholder_is_visual_and_never_enters_search_value(self):
        value=tk.StringVar()
        entry=PlaceholderEntry(self.root,textvariable=value,placeholder='Cerca nei messaggi')
        entry.pack(fill='x');self.layout()
        self.assertEqual(entry.get(),'')
        self.assertEqual(value.get(),'')
        self.assertEqual(entry.hint.winfo_manager(),'place')
        value.set('Testo cercato');self.root.update_idletasks()
        self.assertEqual(entry.get(),'Testo cercato')
        self.assertEqual(entry.hint.winfo_manager(),'')
        value.set('');self.root.update_idletasks()
        self.assertEqual(entry.hint.winfo_manager(),'place')
        entry.destroy();value.set('Ancora valido')

    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.geometry("480x320")
        self.root.attributes("-alpha", 0)
        self.root.attributes("-disabled", True)
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)

    def tearDown(self):
        for event in self.root.tk.call("after", "info"):
            self.root.tk.call("after", "cancel", event)
        self.root.destroy()
        self.assertFalse(self.errors)

    def layout(self):
        self.root.deiconify()
        self.root.update()
        self.root.update_idletasks()

    def test_pack_hides_and_restores_position_and_options(self):
        host = ttk.Frame(self.root)
        host.pack(fill="both", expand=True)
        top = ttk.Label(host, text="Above")
        top.pack(side="top")
        scrollbar = AutoScrollbar(host, orient="horizontal")
        scrollbar.pack(side="top", fill="x", padx=7, pady=3)
        below = ttk.Label(host, text="Below")
        below.pack(side="top")
        original = scrollbar.pack_info()
        scrollbar.set(0, 1)
        self.layout()
        self.assertEqual(scrollbar.winfo_manager(), "")
        scrollbar.set(.1, .8)
        self.layout()
        self.assertEqual(host.pack_slaves(), [top, scrollbar, below])
        self.assertEqual(scrollbar.pack_info(), original)
        self.assertEqual(tuple(map(float, scrollbar.get())), (.1, .8))

    def test_grid_hides_and_restores_same_cell_and_options(self):
        host = ttk.Frame(self.root)
        host.pack(fill="both", expand=True)
        scrollbar = AutoScrollbar(host, orient="vertical")
        scrollbar.grid(row=2, column=3, rowspan=2, sticky="ns", padx=6)
        original = scrollbar.grid_info()
        scrollbar.set("0.0", "1.0")
        self.layout()
        self.assertEqual(scrollbar.winfo_manager(), "")
        scrollbar.set("0", "0.7")
        self.layout()
        self.assertEqual(scrollbar.grid_info(), original)

    def test_range_before_geometry_and_coalesced_layout(self):
        scrollbar = AutoScrollbar(self.root)
        scrollbar.set(0, 1)
        scrollbar.pack(side="right", fill="y")
        self.layout()
        self.assertEqual(scrollbar.winfo_manager(), "")
        scrollbar.set(0, .3)
        scrollbar.set(0, 1)
        scrollbar.set(0, .9)
        self.layout()
        self.assertEqual(scrollbar.winfo_manager(), "pack")
        self.assertIsNone(scrollbar._visibility_callback)
        scrollbar.pack_forget()
        scrollbar.set(0, .4)
        self.layout()
        self.assertEqual(scrollbar.winfo_manager(), "", "Explicit caller hide must be respected")

    def test_real_text_content_and_resize_remain_stable(self):
        host = ttk.Frame(self.root)
        host.pack(fill="both", expand=True)
        host.rowconfigure(0, weight=1)
        host.columnconfigure(0, weight=1)
        text = tk.Text(host, width=1, height=1, wrap="none")
        vertical = AutoScrollbar(host, command=text.yview)
        horizontal = AutoScrollbar(host, orient="horizontal", command=text.xview)
        text.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        text.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        text.insert("1.0", "Short text")
        self.layout()
        self.assertEqual(vertical.winfo_manager(), "")
        self.assertEqual(horizontal.winfo_manager(), "")
        text.insert("end", "\n" + "\n".join("x" * 200 for _ in range(100)))
        self.layout()
        self.assertEqual(vertical.winfo_manager(), "grid")
        self.assertEqual(horizontal.winfo_manager(), "grid")
        for size in ("300x180", "640x400", "420x280"):
            self.root.geometry(size)
            for _ in range(3):
                self.layout()
            self.assertEqual(vertical.winfo_manager(), "grid")
            self.assertEqual(horizontal.winfo_manager(), "grid")
            self.assertIsNone(vertical._visibility_callback)
            self.assertIsNone(horizontal._visibility_callback)
        text.delete("1.0", "end")
        text.insert("1.0", "Fits again")
        for _ in range(3):
            self.layout()
        self.assertEqual(vertical.winfo_manager(), "")
        self.assertEqual(horizontal.winfo_manager(), "")

    def test_destroy_with_pending_visibility_callback_has_no_tcl_error(self):
        scrollbar = AutoScrollbar(self.root)
        scrollbar.pack()
        scrollbar.set(0, 1)
        scrollbar.destroy()
        self.layout()

    def test_source_icons_have_expected_png_and_ico_sizes(self):
        ico, png = app_icon.icon_paths()
        data = ico.read_bytes()
        reserved, kind, count = struct.unpack_from("<HHH", data)
        self.assertEqual((reserved, kind, count), (0, 1, 7))
        sizes = {data[6 + index * 16] or 256 for index in range(count)}
        self.assertEqual(sizes, {16, 24, 32, 48, 64, 128, 256})
        before = {path: path.read_bytes() for path in (ico, png)}
        self.assertTrue(app_icon.set_window_icon(self.root))
        self.assertEqual(self.root._sentinella_icon_image.width(), 512)
        self.assertEqual([image.width() for image in self.root._sentinella_icon_images],[16,32,64,128,256,512])
        for path, contents in before.items():
            self.assertEqual(path.read_bytes(), contents)

    def test_frozen_icon_resolution_uses_bundle_not_data_profile(self):
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary)
            with patch.object(app_icon.sys, "frozen", True, create=True), \
                    patch.object(app_icon.sys, "_MEIPASS", str(bundle), create=True):
                self.assertEqual(app_icon.icon_paths(), (bundle / "assets/sentinella.ico", bundle / "assets/sentinella.png"))
                self.assertFalse(app_icon.set_window_icon(self.root))
            self.assertEqual(list(bundle.iterdir()), [], "Icon helpers must never create files")


if __name__ == "__main__":
    unittest.main()
