"""Read-only binding of native Claude popouts to the application's saved IDs.

about:blank by itself is never identity or origin proof. A root document must
match one app-managed local popout, its native process, title and visible frame.
Unmatched, duplicate, nested, minimized or unqualified windows fail closed.
"""
import json
import math
import os
from pathlib import Path,PureWindowsPath
import uuid


def match_session(records,title,frame):
    if not isinstance(frame,(list,tuple)) or len(frame)!=4 or any(not isinstance(v,(int,float)) or isinstance(v,bool) or not math.isfinite(v) for v in frame):return None
    found=[]
    for row in records:
        if not isinstance(row,dict) or row.get('sessionType')!='local' or row.get('isSsh') is not False or row.get('title')!=title:
            continue
        bounds=row.get('bounds',{})
        if not isinstance(bounds,dict):continue
        values=[bounds.get(k) for k in ('x','y','width','height')]
        if any(not isinstance(v,(int,float)) or isinstance(v,bool) or not math.isfinite(v) for v in values):continue
        if values[2]<=0 or values[3]<=0:continue
        # DWM includes the thin visible outline; compare individual edges.
        expected=(values[0],values[1],values[0]+values[2],values[1]+values[3])
        actual=(frame[0],frame[1],frame[0]+frame[2],frame[1]+frame[3])
        if any(abs(a-b)>1 for a,b in zip(expected,actual)):continue
        identifier=row.get('sessionId','')
        if not isinstance(identifier,str) or not identifier.startswith('local_'):continue
        identifier=identifier.removeprefix('local_')
        try:
            if str(uuid.UUID(identifier))!=identifier:continue
        except ValueError:continue
        found.append(identifier)
    return found[0] if len(found)==1 else None


def visible_frame(handle):
    import ctypes as ct
    from ctypes import wintypes as wt
    user=ct.WinDLL('user32');dwm=ct.WinDLL('dwmapi')
    user.GetDpiForWindow.argtypes=[wt.HWND];user.GetDpiForWindow.restype=wt.UINT
    user.IsIconic.argtypes=[wt.HWND]
    if user.IsIconic(handle) or user.GetDpiForWindow(handle)!=96:return None
    dwm.DwmGetWindowAttribute.argtypes=[wt.HWND,wt.DWORD,ct.c_void_p,wt.DWORD]
    dwm.DwmGetWindowAttribute.restype=ct.c_long
    rect=wt.RECT()
    if dwm.DwmGetWindowAttribute(handle,9,ct.byref(rect),ct.sizeof(rect))!=0:return None
    frame=(rect.left,rect.top,rect.right-rect.left,rect.bottom-rect.top)
    return frame if frame[2]>0 and frame[3]>0 else None


def root_window(document):
    node=document.GetParentControl()
    for _ in range(32):
        if node is None or node.ControlTypeName=='DocumentControl':return None
        if node.ControlTypeName=='WindowControl':return node
        node=node.GetParentControl()
    return None


def records_for_executable(executable):
    from desktop_identity import PACKAGE
    package=next((PACKAGE.fullmatch(part) for part in PureWindowsPath(executable).parts if PACKAGE.fullmatch(part)),None)
    if package:
        state=Path(os.environ['LOCALAPPDATA'])/'Claude-Data'/('claude_'+package.group(5))/'session-popouts.json'
    else:
        state=Path(os.environ['APPDATA'])/'Claude/session-popouts.json'
    if state.stat().st_size>262144:return []
    data=json.loads(state.read_text(encoding='utf-8'))
    return data.get('popouts',[]) if data.get('version')==1 and isinstance(data.get('popouts'),list) else []


def session_for_document(document):
    try:
        import psutil
        import uiautomation as auto
        from automation_policy import load_config
        from desktop_identity import executable_matches,app_window_handles
        window=root_window(document)
        if window is None or window.ClassName!='Chrome_WidgetWin_1' or window.Name!=document.Name:return None
        executable=psutil.Process(window.ProcessId).exe()
        expected=load_config().get('apps',{}).get('claude',{}).get('process_path')
        if not expected or not executable_matches(executable,expected):return None
        frame=visible_frame(window.NativeWindowHandle)
        if frame is None:return None
        identifier=match_session(records_for_executable(executable),window.Name,frame)
        if not identifier:return None
        # An identically named window at the same frame cannot inherit a record.
        for handle in app_window_handles(expected):
            if handle==window.NativeWindowHandle:continue
            other=auto.ControlFromHandle(handle)
            if other.Name==window.Name and visible_frame(handle)==frame:return None
        return identifier
    except (OSError,ValueError,TypeError,AttributeError,KeyError):
        return None
