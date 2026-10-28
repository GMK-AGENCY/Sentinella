"""Keep an app's configured identity across Windows Store version updates."""
from pathlib import PureWindowsPath
import re


PACKAGE = re.compile(r"^(.+)_(\d+\.\d+\.\d+\.\d+)_([^_]+)_([^_]*)_([^_]+)$")


def executable_matches(actual, configured):
    actual, configured = PureWindowsPath(actual), PureWindowsPath(configured)
    if actual == configured:
        return True

    def identity(path):
        for index, part in enumerate(path.parts):
            package = PACKAGE.fullmatch(part)
            if package and index and path.parts[index - 1].casefold() == "windowsapps":
                name, _, arch, resource, publisher = package.groups()
                return (tuple(p.casefold() for p in path.parts[:index]),
                        name.casefold(), arch.casefold(), resource.casefold(), publisher.casefold(),
                        tuple(p.casefold() for p in path.parts[index + 1:]))
        return None

    expected = identity(configured)
    return expected is not None and identity(actual) == expected


def app_window_handles(configured):
    """Filter HWNDs by executable before contacting an accessibility provider."""
    import ctypes
    from ctypes import wintypes
    import psutil
    user = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user.EnumWindows.restype = wintypes.BOOL
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    result, processes = [], {}

    @callback_type
    def collect(handle, _):
        if not user.IsWindowVisible(handle):
            return True
        pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        try:
            if pid.value not in processes:
                processes[pid.value] = executable_matches(psutil.Process(pid.value).exe(), configured)
            if processes[pid.value]:
                result.append(int(handle))
        except (psutil.Error, OSError):
            pass
        return True

    if not user.EnumWindows(collect, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    return result


def foreground_process_id():
    import ctypes
    from ctypes import wintypes
    user = ctypes.windll.user32
    user.GetForegroundWindow.restype = wintypes.HWND
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    pid = wintypes.DWORD()
    user.GetWindowThreadProcessId(user.GetForegroundWindow(), ctypes.byref(pid))
    return pid.value
