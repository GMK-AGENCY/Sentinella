"""Bound native adapters even when a Windows Python child retains pipe handles.

Use files for output: subprocess.run(timeout=...) kills only the redirector and
then waits without a deadline for inherited stdout/stderr pipes to close.
"""
import subprocess
import logging
import tempfile
import time

import psutil


def stop_owned(process, known):
    parent=known.get(process.pid)
    try:
        if parent is None and process.poll() is None:
            candidate=psutil.Process(process.pid)
            candidate.create_time()  # Pin identity before any enumeration.
            if process.poll() is None:parent=candidate;known[parent.pid]=parent
        if parent is not None and parent.is_running():
            for child in parent.children(recursive=True):
                child.create_time();known[child.pid]=child
    except psutil.Error:pass
    for child in reversed(list(known.values())):
        try:
            child.kill()
        except psutil.Error:
            pass
    verified=False
    try:
        _,alive=psutil.wait_procs(list(known.values()), timeout=1)
        verified=not alive
        if alive:logging.getLogger(__name__).warning('Chiusura non verificata per i processi propri: %s',[p.pid for p in alive])
    except psutil.Error as exc:
        # Windows may deny observation while a killed child is exiting. Do
        # not retry or elevate, and never mistake failed observation for exit.
        logging.getLogger(__name__).warning('Attesa di chiusura non verificabile, nessun nuovo tentativo: %s',exc)
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        verified=False
    return verified


def run_bounded(command, timeout=12, cwd=None):
    """Return bounded output; terminate only this invocation's process tree."""
    if not 0 < timeout <= 50:
        raise ValueError("Native worker timeout must be within 0..50 seconds")
    started = time.monotonic()
    known = {}
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(command, cwd=cwd, stdout=stdout, stderr=stderr,
                                   stdin=subprocess.DEVNULL,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        timed_out = False
        try:
            parent = psutil.Process(process.pid)
            known[parent.pid] = parent
            while process.poll() is None:
                try:
                    for child in parent.children(recursive=True):
                        known[child.pid] = child
                except psutil.Error:
                    pass
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    timed_out = True
                    break
                try:
                    process.wait(timeout=min(.05, remaining))
                except subprocess.TimeoutExpired:
                    pass
        finally:
            # A successful adapter must not leave a background grandchild either.
            cleanup_verified=stop_owned(process, known)
        def tail(stream):
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size - 524288))
            return stream.read().decode("utf-8", errors="replace")
        return {"returncode": process.returncode, "stdout": tail(stdout), "stderr": tail(stderr),
                "timed_out": timed_out, "cleanup_verified":cleanup_verified,
                "elapsed_seconds": round(time.monotonic() - started, 3)}
