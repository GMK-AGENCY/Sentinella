"""Stock Tk controls and immutable source/frozen assets; no desktop actions."""
from pathlib import Path
import struct
import tempfile
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

import app_icon
from ui_components import AutoScrollbar


class UIComponentsTests(unittest.TestCase):
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
