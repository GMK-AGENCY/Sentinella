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
from ui_components import AutoScrollbar, PlaceholderEntry


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
