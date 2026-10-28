"""Bound native adapters even when a Windows Python child retains pipe handles.

Use files for output: subprocess.run(timeout=...) kills only the redirector and
then waits without a deadline for inherited stdout/stderr pipes to close.
"""
import subprocess
import tempfile
import time

import psutil


def stop_owned(process, known):
    try:
        parent = psutil.Process(process.pid)
        for child in parent.children(recursive=True):
            known[child.pid] = child
        known[parent.pid] = parent
    except psutil.Error:
        pass
    for child in reversed(list(known.values())):
        try:
            child.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(list(known.values()), timeout=1)
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass


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
            stop_owned(process, known)
        def tail(stream):
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size - 524288))
            return stream.read().decode("utf-8", errors="replace")
        return {"returncode": process.returncode, "stdout": tail(stdout), "stderr": tail(stderr),
                "timed_out": timed_out, "elapsed_seconds": round(time.monotonic() - started, 3)}
