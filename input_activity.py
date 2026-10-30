"""Passive input-origin observations; never store keys, text or coordinates.

Only a recent, matching observation may override GetLastInputInfo. Hook loss,
unknown events, secure desktops and unsupported input use the conservative API.
"""
import ctypes as ct
from ctypes import wintypes as wt
import json
import math
import os
from pathlib import Path
import threading
import time
import uuid


def trusted_idle(record, run_id, tick, last_input):
    if not isinstance(record,dict):return None
    if not run_id or record.get('run_id') != run_id or record.get('source') != 'input-hooks':
        return None
    observed, physical = record.get('tick'), record.get('last_device_tick')
    if any(not isinstance(v,(int,float)) or isinstance(v,bool) or not math.isfinite(v) for v in (observed,physical)):
        return None
    if not 0 <= tick-observed <= 3000 or not 0 <= physical <= observed:
        return None
    if record.get('all_input_tick') != last_input or record.get('last_hook_tick') != last_input:
        return None  # A new/unseen event or a removed hook cannot infer absence.
    return (tick-physical)/1000


def device_idle(root, tick, last_input):
    try:
        record=json.loads((Path(root)/'logs/input-health.json').read_text(encoding='utf-8'))
        return trusted_idle(record,os.environ.get('SENTINELLA_RUN_ID'),tick,last_input)
    except (OSError,ValueError,TypeError):
        return None


class DeviceActivity:
    def __init__(self, root, run_id):
        self.root=Path(root);self.run_id=run_id
        self.ready=threading.Event();self.window=None;self.error=None
        self.last_device_tick=0;self.last_hook_tick=0;self.events=0;self.injected=0
        self.source='starting'
        self.thread=threading.Thread(target=self._run,daemon=True,name='Sentinella-input-origin')

    def start(self):
        self.thread.start();self.ready.wait(1)
        return self

    def record_origin(self, flags, stamp, injected_mask, tick):
        self.last_hook_tick=stamp
        if flags&injected_mask:self.injected+=1
        else:self.events+=1;self.last_device_tick=tick

    def stop(self):
        if self.window and self.thread.is_alive():
            user=ct.windll.user32
            user.PostMessageW.argtypes=[wt.HWND,wt.UINT,wt.WPARAM,wt.LPARAM]
            user.PostMessageW(self.window,0x0010,0,0)
        self.thread.join(2)
        if not self.thread.is_alive():self.window=None

    def publish(self, user, kernel):
        from sentinel_launcher import atomic_json
        from question_desktop import desktop_is_unlocked
        class LastInput(ct.Structure):
            _fields_=[('cbSize',wt.UINT),('dwTime',wt.DWORD)]
        info=LastInput(ct.sizeof(LastInput),0);tick=int(kernel.GetTickCount64())
        if not user.GetLastInputInfo(ct.byref(info)):
            self.error='GetLastInputInfo non disponibile'
        if not desktop_is_unlocked():self.last_device_tick=tick
        qualified = not self.error and int(info.dwTime)==self.last_hook_tick
        try:
            atomic_json(self.root/'logs/input-health.json',dict(run_id=self.run_id,pid=os.getpid(),
                        updated_at=time.time(),tick=tick,last_device_tick=self.last_device_tick,
                        all_input_tick=int(info.dwTime),last_hook_tick=self.last_hook_tick,
                        events=self.events,injected_events=self.injected,
                        source=self.source if qualified else 'fallback',error=self.error))
        except OSError:
            pass

    def _run(self):
        try:self._pump()
        except Exception as exc:self.error=f'{type(exc).__name__}: {exc}'
        finally:self.ready.set()

    def _pump(self):
        user=ct.WinDLL('user32',use_last_error=True);kernel=ct.WinDLL('kernel32',use_last_error=True)
        kernel.GetTickCount64.restype=ct.c_ulonglong
        if user.GetSystemMetrics(0x1000) or user.GetSystemMetrics(94)&0x0f:
            self.error='Sorgente non qualificata per desktop remoto o touch';self.publish(user,kernel);return
        wndproc=ct.WINFUNCTYPE(ct.c_ssize_t,wt.HWND,wt.UINT,wt.WPARAM,wt.LPARAM)
        hookproc=ct.WINFUNCTYPE(ct.c_ssize_t,ct.c_int,wt.WPARAM,wt.LPARAM)
        class WindowClass(ct.Structure):
            _fields_=[('style',wt.UINT),('proc',wndproc),('classExtra',ct.c_int),('windowExtra',ct.c_int),
                      ('instance',wt.HINSTANCE),('icon',wt.HICON),('cursor',wt.HANDLE),('brush',wt.HBRUSH),
                      ('menu',wt.LPCWSTR),('name',wt.LPCWSTR)]
        class LastInput(ct.Structure):
            _fields_=[('cbSize',wt.UINT),('dwTime',wt.DWORD)]
        user.DefWindowProcW.argtypes=[wt.HWND,wt.UINT,wt.WPARAM,wt.LPARAM];user.DefWindowProcW.restype=ct.c_ssize_t
        user.CreateWindowExW.argtypes=[wt.DWORD,wt.LPCWSTR,wt.LPCWSTR,wt.DWORD,ct.c_int,ct.c_int,ct.c_int,ct.c_int,wt.HWND,wt.HMENU,wt.HINSTANCE,ct.c_void_p]
        user.CreateWindowExW.restype=wt.HWND
        user.SetWindowsHookExW.argtypes=[ct.c_int,hookproc,wt.HINSTANCE,wt.DWORD];user.SetWindowsHookExW.restype=wt.HANDLE
        user.UnhookWindowsHookEx.argtypes=[wt.HANDLE]
        user.CallNextHookEx.argtypes=[wt.HANDLE,ct.c_int,wt.WPARAM,wt.LPARAM];user.CallNextHookEx.restype=ct.c_ssize_t
        kernel.GetModuleHandleW.argtypes=[wt.LPCWSTR];kernel.GetModuleHandleW.restype=wt.HMODULE
        user.DestroyWindow.argtypes=[wt.HWND];user.UnregisterClassW.argtypes=[wt.LPCWSTR,wt.HINSTANCE]
        user.GetMessageW.argtypes=[ct.POINTER(wt.MSG),wt.HWND,wt.UINT,wt.UINT]
        user.TranslateMessage.argtypes=[ct.POINTER(wt.MSG)]
        user.DispatchMessageW.argtypes=[ct.POINTER(wt.MSG)];user.DispatchMessageW.restype=ct.c_ssize_t
        user.SetTimer.argtypes=[wt.HWND,ct.c_size_t,wt.UINT,ct.c_void_p];user.SetTimer.restype=ct.c_size_t
        user.KillTimer.argtypes=[wt.HWND,ct.c_size_t]
        info=LastInput(ct.sizeof(LastInput),0);now=int(kernel.GetTickCount64())
        self.last_device_tick=now
        if user.GetLastInputInfo(ct.byref(info)):
            self.last_hook_tick=int(info.dwTime)

        def observe(code,wparam,lparam,flags_offset,time_offset,injected_mask):
            if code>=0:
                try:
                    # Read only origin flags and timestamp, never keyboard codes.
                    flags=wt.DWORD.from_address(lparam+flags_offset).value
                    stamp=wt.DWORD.from_address(lparam+time_offset).value
                    self.record_origin(flags,stamp,injected_mask,int(kernel.GetTickCount64()))
                except Exception:self.error='Osservazione input incompleta'
            return user.CallNextHookEx(None,code,wparam,lparam)

        @hookproc
        def keyboard(code,wparam,lparam):return observe(code,wparam,lparam,8,12,0x10)
        @hookproc
        def mouse(code,wparam,lparam):return observe(code,wparam,lparam,12,16,0x01)
        @wndproc
        def proc(hwnd,message,wparam,lparam):
            if message==0x0113:self.publish(user,kernel);return 0
            if message==0x0010:user.DestroyWindow(hwnd);return 0
            if message==0x0002:self.source='stopped';self.publish(user,kernel);user.PostQuitMessage(0);return 0
            return user.DefWindowProcW(hwnd,message,wparam,lparam)

        instance=kernel.GetModuleHandleW(None);name='SentinellaInput'+uuid.uuid4().hex
        window_class=WindowClass(0,proc,0,0,instance,None,None,None,None,name)
        if not user.RegisterClassW(ct.byref(window_class)):raise ct.WinError(ct.get_last_error())
        self.window=user.CreateWindowExW(0,name,'Sentinella input observer',0,0,0,0,0,wt.HWND(-3),None,instance,None)
        if not self.window:raise ct.WinError(ct.get_last_error())
        hooks=[];timer=0
        try:
            for kind,callback in ((13,keyboard),(14,mouse)):
                handle=user.SetWindowsHookExW(kind,callback,instance,0)
                if not handle:raise ct.WinError(ct.get_last_error())
                hooks.append(handle)
            self.source='input-hooks';self.publish(user,kernel);self.ready.set()
            timer=user.SetTimer(self.window,1,1000,None)
            if not timer:raise ct.WinError(ct.get_last_error())
            message=wt.MSG()
            while user.GetMessageW(ct.byref(message),None,0,0)>0:
                user.TranslateMessage(ct.byref(message));user.DispatchMessageW(ct.byref(message))
        finally:
            if timer:user.KillTimer(self.window,timer)
            for handle in hooks:user.UnhookWindowsHookEx(handle)
            user.UnregisterClassW(name,instance)
