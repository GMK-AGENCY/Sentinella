"""Clearly labelled native consent fixture; never impersonates a real Codex chat.

Its Write continuation only creates a fixed marker in test-project. Run with
--once to exercise the fallback. Test configuration is separate from production.
"""
import argparse
import ctypes as ct
from ctypes import wintypes as wt
import json
import os
import psutil

from runtime_paths import data_root
ROOT = data_root()


def run(once):
    user32, kernel32 = ct.windll.user32, ct.windll.kernel32
    callback = ct.WINFUNCTYPE(ct.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
    class WNDCLASS(ct.Structure):
        _fields_ = [("style", wt.UINT), ("lpfnWndProc", callback), ("cbClsExtra", ct.c_int), ("cbWndExtra", ct.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON), ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]
    user32.DefWindowProcW.restype = ct.c_ssize_t
    user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    user32.CreateWindowExW.restype = wt.HWND
    user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ct.c_int, ct.c_int, ct.c_int, ct.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, ct.c_void_p]
    kernel32.GetModuleHandleW.restype = wt.HMODULE
    kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
    mode = "una-volta" if once else "sempre"
    marker = ROOT / "test-project" / f"codex-consenso-SIMULATO-{mode}.txt"
    @callback
    def proc(hwnd, message, wparam, lparam):
        if message == 0x0111:
            button = wparam & 0xFFFF
            if button in {101, 102}:
                marker.write_text(f"SIMULAZIONE_CODEX_RIPARTITA: {mode}\n", encoding="utf-8")
                (ROOT / "logs" / f"simulazione-{mode}-risultato.json").write_text(json.dumps({"simulation": True, "continued": True, "button": "Consenti sempre" if button == 101 else "Consenti una volta", "marker": str(marker)}), encoding="utf-8")
                user32.DestroyWindow(hwnd)
                return 0
            if button == 103:
                user32.DestroyWindow(hwnd)
                return 0
        if message == 0x0002:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, message, wparam, lparam)
    instance = kernel32.GetModuleHandleW(None)
    name = f"SentinellaConsentFixture{os.getpid()}"
    wc = WNDCLASS(0, proc, 0, 0, instance, None, None, wt.HBRUSH(6), None, name)
    user32.RegisterClassW(ct.byref(wc))
    hwnd = user32.CreateWindowExW(0, name, "Sentinella - SIMULAZIONE consenso Codex", 0x10CF0000, 140, 140, 850, 310, None, None, instance, None)
    text = f"SIMULAZIONE ESPLICITA. Non è una richiesta di Codex Desktop.\nwrite file (solo questa sessione)\n{marker}"
    user32.CreateWindowExW(0, "STATIC", text, 0x50000000, 20, 25, 790, 130, hwnd, None, instance, None)
    buttons = [(102, "Consenti una volta"), (103, "Nega")]
    if not once:
        buttons.insert(0, (101, "Consenti sempre"))
    for index, (control_id, label) in enumerate(buttons):
        user32.CreateWindowExW(0, "BUTTON", label, 0x50010000, 20 + index * 235, 190, 220, 40, hwnd, wt.HMENU(control_id), instance, None)
    config = json.loads((ROOT / "approval-config.json").read_text(encoding="utf-8"))
    config.update(enabled=True, allowed_project_roots=[str(ROOT / "test-project")])
    config["apps"]["codex"] = {"enabled": True, "process_path": psutil.Process().exe()}
    config["simulation_window_handle"] = int(hwnd)
    (ROOT / "logs" / "approval-simulation-config.json").write_text(json.dumps(config), encoding="utf-8")
    message = wt.MSG()
    while user32.GetMessageW(ct.byref(message), None, 0, 0) > 0:
        user32.TranslateMessage(ct.byref(message))
        user32.DispatchMessageW(ct.byref(message))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    run(parser.parse_args().once)
