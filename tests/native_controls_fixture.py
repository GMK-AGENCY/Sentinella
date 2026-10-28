"""Visible, read-only native fixtures. No button grants access or writes files."""
import ctypes as ct
from ctypes import wintypes as wt
import json
from pathlib import Path
import sys


def run(ready):
    user, kernel = ct.windll.user32, ct.windll.kernel32
    callback = ct.WINFUNCTYPE(ct.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

    class WNDCLASS(ct.Structure):
        _fields_ = [("style", wt.UINT), ("proc", callback), ("extra", ct.c_int), ("windowextra", ct.c_int),
                    ("instance", wt.HINSTANCE), ("icon", wt.HICON), ("cursor", wt.HANDLE),
                    ("background", wt.HBRUSH), ("menu", wt.LPCWSTR), ("name", wt.LPCWSTR)]

    user.DefWindowProcW.restype = ct.c_ssize_t
    user.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    user.CreateWindowExW.restype = wt.HWND
    user.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ct.c_int, ct.c_int, ct.c_int, ct.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, ct.c_void_p]
    kernel.GetModuleHandleW.restype = wt.HMODULE
    kernel.GetModuleHandleW.argtypes = [wt.LPCWSTR]

    @callback
    def proc(hwnd, message, wparam, lparam):
        # Automatic close after 15 seconds even if the diagnostic parent fails.
        if message in (0x0113, 0x0010):
            user.DestroyWindow(hwnd)
            return 0
        if message == 0x0111:
            Path(ready).with_suffix('.unexpected-click').write_text('Unexpected fixture input')
            return 0
        return user.DefWindowProcW(hwnd, message, wparam, lparam)

    instance = kernel.GetModuleHandleW(None)
    name = "SentinellaReadOnlyFixture"
    wc = WNDCLASS(0, proc, 0, 0, instance, None, None, wt.HBRUSH(6), None, name)
    user.RegisterClassW(ct.byref(wc))

    def window(title, y):
        hwnd = user.CreateWindowExW(0, name, title, 0x10CF0000, 170, y, 760, 270, None, None, instance, None)
        user.SetTimer(hwnd, 1, 15000, None)
        return hwnd

    def control(hwnd, kind, text, x, y, width=680, height=35):
        return user.CreateWindowExW(0, kind, text, 0x50000000, x, y, width, height, hwnd, None, instance, None)

    question = window("Sentinella - PROVA LOCALE domanda (sola lettura)", 120)
    control(question, "STATIC", "Domanda", 15, 10)
    control(question, "STATIC", "Verifica locale: il controllore riconosce questa domanda?", 15, 45)
    control(question, "STATIC", "Rispondi", 15, 85, 100)
    control(question, "EDIT", "", 130, 85, 550)
    control(question, "BUTTON", "Salta", 15, 150, 180)
    control(question, "BUTTON", "Invia", 220, 150, 180)
    consent = window("Sentinella - PROVA LOCALE consenso (sola lettura)", 420)
    marker = Path(__file__).resolve().parents[1] / "test-project" / "native-readonly-fixture.txt"
    control(consent, "STATIC", "PROVA INERTE: nessuna operazione viene eseguita.\nwrite file (solo questa sessione)\n" + str(marker), 15, 10, height=110)
    control(consent, "BUTTON", "Consenti una volta", 15, 145, 200)
    control(consent, "BUTTON", "Nega", 230, 145, 180)
    Path(ready).write_text(json.dumps({"question": int(question), "consent": int(consent)}))
    message = wt.MSG()
    while user.GetMessageW(ct.byref(message), None, 0, 0) > 0:
        user.TranslateMessage(ct.byref(message))
        user.DispatchMessageW(ct.byref(message))


if __name__ == "__main__":
    run(sys.argv[1])
