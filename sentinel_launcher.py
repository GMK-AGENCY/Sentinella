"""One per-user launcher restarts a crashed or unresponsive Sentinella UI."""
import argparse
from datetime import datetime
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
import psutil

from runtime_paths import data_root, command
ROOT = data_root()


def atomic_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    # Windows readers can briefly hold the destination without delete sharing.
    # Preserve the old, valid file and retry replacement for at most150ms.
    for attempt in range(5):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(.01 * 2 ** attempt)


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def stop_tree(child):
    try:
        process = psutil.Process(child.pid)
        descendants = process.children(recursive=True)
        for item in [*reversed(descendants), process]:
            try:
                item.terminate()
            except psutil.Error:
                pass
        _, alive = psutil.wait_procs([*descendants, process], timeout=2)
        for item in alive:
            try:
                item.kill()
            except psutil.Error:
                pass
    except psutil.Error:
        pass


def cleanup_owned_workers(root, run_id):
    owned = []
    for process in psutil.process_iter(["name", "cmdline"]):
        try:
            command = " ".join(process.info["cmdline"] or [])
            from runtime_paths import frozen
            if frozen():
                if Path(process.exe()).resolve() != Path(sys.executable).resolve() or "--process sentinel_launcher" in command:
                    continue
            else:
                if (process.info["name"] or "").casefold() not in {"python.exe", "pythonw.exe"}:
                    continue
                if str(root).casefold() not in command.casefold() or "sentinel_launcher.py" in command:
                    continue
            if process.environ().get("SENTINELLA_RUN_ID") == run_id:
                process.terminate()
                owned.append(process)
        except psutil.Error:
            continue
    _, alive = psutil.wait_procs(owned, timeout=2)
    for process in alive:
        try:
            process.kill()
        except psutil.Error:
            pass


class HeartbeatWatch:
    """A transient read failure does not erase a recently observed heartbeat."""
    def __init__(self, run_id):
        self.run_id = run_id
        self.timestamp = None

    def observe(self, value):
        if not isinstance(value, dict) or value.get("run_id") != self.run_id:
            return
        stamp = value.get("updated_at")
        if not isinstance(stamp, (int, float)) or not math.isfinite(stamp):
            return
        if "poll_updated_at" in value:
            poll_stamp = value["poll_updated_at"]
            if not isinstance(poll_stamp, (int, float)) or not math.isfinite(poll_stamp):
                return
            stamp = min(stamp, poll_stamp)
        if self.timestamp is None or stamp >= self.timestamp:
            self.timestamp = stamp

    def fresh(self, now, timeout=45):
        return self.timestamp is not None and 0 <= now - self.timestamp <= timeout


class Launcher:
    def __init__(self, root=ROOT, executable=sys.executable, background=False, check_seconds=2, restart_scale=1, watchdog_grace=90, heartbeat_timeout=45):
        self.root = Path(root)
        self.logs = self.root / "logs"
        self.logs.mkdir(exist_ok=True)
        self.executable = executable
        self.background = background
        self.check_seconds = check_seconds
        self.restart_scale = restart_scale
        self.watchdog_grace = watchdog_grace
        self.heartbeat_timeout = heartbeat_timeout

    def log(self, **record):
        with (self.logs / "launcher.jsonl").open("a", encoding="utf-8") as out:
            out.write(json.dumps({"time": datetime.now().astimezone().isoformat(), **record}, ensure_ascii=False) + "\n")

    def run(self):
        failures = 0
        while True:
            run_id = str(uuid.uuid4())
            env = {**os.environ, "SENTINELLA_RUN_ID": run_id}
            arguments = command("notification_watch", executable=self.executable, source_root=self.root)
            if self.background:
                arguments.append("--background")
            try:
                child = subprocess.Popen(arguments, cwd=self.root, env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except OSError as exc:
                self.log(event="launch_failed", error=str(exc))
                failures += 1
                time.sleep(min(60, 5 * 2 ** min(failures - 1, 4)) * self.restart_scale)
                continue
            started = time.monotonic()
            watch = HeartbeatWatch(run_id)
            self.log(event="started", child_pid=child.pid, run_id=run_id)
            atomic_json(self.logs / "launcher-health.json", {"pid": os.getpid(), "child_pid": child.pid, "run_id": run_id, "started_at": time.time()})
            while child.poll() is None:
                heartbeat = read_json(self.logs / "gui-heartbeat.json")
                watch.observe(heartbeat)
                # A fresh launch has a bounded grace period for Python/Tk startup.
                if time.monotonic() - started > self.watchdog_grace:
                    if not watch.fresh(time.time(), self.heartbeat_timeout):
                        self.log(event="unresponsive", child_pid=child.pid, run_id=run_id, last_valid_heartbeat=watch.timestamp)
                        stop_tree(child)
                        break
                time.sleep(self.check_seconds)
            intent = read_json(self.logs / "exit-intent.json")
            cleanup_owned_workers(self.root, run_id)
            if intent.get("run_id") == run_id:
                self.log(event="closed_by_user", run_id=run_id)
                return
            if time.monotonic() - started >= 120:
                failures = 0
            failures += 1
            delay = min(60, 5 * 2 ** min(failures - 1, 4)) * self.restart_scale
            self.log(event="restart_scheduled", exit_code=child.poll(), delay_seconds=delay, run_id=run_id)
            time.sleep(delay)


def main():
    from config_store import ensure_local_files
    ensure_local_files(ROOT)
    parser = argparse.ArgumentParser()
    parser.add_argument("--background", action="store_true")
    args = parser.parse_args()
    import msvcrt
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    lock = (logs / "launcher.lock").open("a+b")
    if lock.tell() == 0:
        lock.write(b"0")
        lock.flush()
    lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        if not args.background:
            atomic_json(logs / "show-window.json", {"requested_at": time.time()})
        return
    (logs / "launcher.pid").write_text(str(os.getpid()), encoding="ascii")
    Launcher(background=args.background).run()


if __name__ == "__main__":
    main()
