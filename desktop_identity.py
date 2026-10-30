"""Keep an app's configured identity across Windows Store version updates."""
from pathlib import PureWindowsPath
import re


PACKAGE = re.compile(r"^(.+)_(\d+\.\d+\.\d+\.\d+)_([^_]+)_([^_]*)_([^_]+)$")


def claude_document_session(control):
    from urllib.parse import urlparse
    import uuid
    if control.ControlTypeName != 'DocumentControl':return None
    try:
        value=control.GetValuePattern().Value
        if value=='about:blank':
            from claude_popouts import session_for_document
            return session_for_document(control)
        url=urlparse(value)
        if url.scheme!='https' or url.hostname!='claude.ai' or not url.path.startswith('/epitaxy/local_'):return None
        value=url.path.removeprefix('/epitaxy/local_')
        return value if str(uuid.UUID(value))==value else None
    except Exception:return None


def native_document(control,app):
    """The nearest document is decisive: an embedded page cannot inherit trust."""
    node=control
    for _ in range(32):
        if node is None:return None
        if node.ControlTypeName=='DocumentControl':
            if app=='claude':return node if claude_document_session(node) else None
            try:return node if node.GetValuePattern().Value=='app://-/index.html' else None
            except Exception:return None
        try:node=node.GetParentControl()
        except Exception:return None
    return None


def document_title(document, controls, app):
    """Read a renamed Claude label from its native session toolbar.

    Popout window/document names can retain their old value after a rename.
    This changes only the display label; session identity still comes from ID.
    """
    fallback = document.Name.removesuffix(' - Claude Code')
    if app != 'claude':
        return fallback
    titles = []
    for control in controls:
        if control.ControlTypeName != 'ButtonControl' or not control.Name.endswith((', rinomina la sessione', ', rename session')):
            continue
        owner = native_document(control, app)
        if owner is None or tuple(owner.GetRuntimeId()) != tuple(document.GetRuntimeId()):
            continue
        heading = control.GetParentControl()
        toolbar = heading.GetParentControl() if heading is not None else None
        # Claude wraps the heading in an anonymous layout group in popouts.
        for _ in range(3):
            if toolbar is None or toolbar.AriaRole == 'toolbar':
                break
            if toolbar.ControlTypeName != 'GroupControl' or toolbar.Name:
                break
            toolbar = toolbar.GetParentControl()
        if heading is None or toolbar is None or heading.AriaRole != 'heading' or toolbar.AriaRole != 'toolbar':
            continue
        if toolbar.Name not in {'Strumenti della sessione', 'Session tools'}:
            continue
        if control.Name in {heading.Name + ', rinomina la sessione', heading.Name + ', rename session'}:
            titles.append(heading.Name)
    return titles[0] if len(titles) == 1 else fallback


def document_controls(controls,document):
    """One bounded ancestry cache excludes other panes and embedded web views."""
    import time
    target=tuple(document.GetRuntimeId());cache={target:True};result=[document]
    deadline=time.monotonic()+2
    for control in controls:
        if time.monotonic()>deadline:raise RuntimeError('Documento della chat non verificato entro il limite')
        chain=[];node=control;belongs=False
        for _ in range(32):
            if node is None:break
            try:
                key=tuple(node.GetRuntimeId())
                if key in cache:belongs=cache[key];break
                chain.append(key)
                if node.ControlTypeName=='DocumentControl':break
                node=node.GetParentControl()
            except Exception:break
        for key in chain:cache[key]=belongs
        if belongs and tuple(control.GetRuntimeId())!=target:result.append(control)
    return result


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
