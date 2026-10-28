"""Read-only measurement of Sentinella's owned process tree."""
import argparse
import json
from pathlib import Path
import time
import psutil

from runtime_paths import data_root
ROOT = data_root()


def measure(seconds):
    pid = int((ROOT / "logs" / "monitor.pid").read_text())
    parent = psutil.Process(pid)
    if Path(parent.cwd()).resolve() != ROOT or not any("notification_watch.py" in arg for arg in parent.cmdline()):
        raise RuntimeError("Il PID non appartiene a Sentinella")
    processes = [parent, *parent.children(recursive=True)]
    tracked = {p.pid: p for p in processes}
    def sample():
        cpu, rss, private = {}, 0, 0
        for process in tracked.values():
            try:
                times = process.cpu_times()
                cpu[process.pid] = times.user + times.system
                memory = process.memory_info()
                rss += memory.rss
                private += getattr(memory, "private", memory.vms)
            except psutil.Error:
                pass
        return cpu, rss, private
    initial, _, _ = sample()
    start = time.perf_counter()
    health_samples = []
    peaks = [0, 0]
    while time.perf_counter() - start < seconds:
        time.sleep(max(0, min(1, seconds - (time.perf_counter() - start))))
        _, rss, private = sample()
        peaks = [max(peaks[0], rss), max(peaks[1], private)]
        try:
            health = json.loads((ROOT / "logs" / "health.json").read_text())
            health_samples.append({k: health.get(k) for k in ("last_successful_scan", "restarts", "worker_pid")})
        except (OSError, ValueError):
            pass
    final, _, _ = sample()
    elapsed = time.perf_counter() - start
    cpu_seconds = sum(max(0, final.get(pid, value) - value) for pid, value in initial.items())
    scans = list(dict.fromkeys(row["last_successful_scan"] for row in health_samples))
    config = json.loads((ROOT / "approval-config.json").read_text())
    return {"elapsed_seconds": round(elapsed, 2), "paused": config.get("paused"), "cpu_seconds": round(cpu_seconds, 4), "cpu_by_pid": {str(pid): round(max(0, final.get(pid, value) - value), 4) for pid, value in initial.items()}, "cpu_percent_one_core": round(cpu_seconds / elapsed * 100, 3), "cpu_percent_machine": round(cpu_seconds / elapsed * 100 / psutil.cpu_count(), 3), "peak_private_mb": round(peaks[1] / 1048576, 2), "peak_rss_mb_shared_pages_counted_per_process": round(peaks[0] / 1048576, 2), "processes": len(processes), "notification_scan_timestamps": scans, "reader_restarts": max((r.get("restarts", 0) for r in health_samples), default=None), "reader_pids": list({r.get("worker_pid") for r in health_samples})}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=35)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 55:
        parser.error("La misura deve durare da 1 a 55 secondi")
    result = measure(args.seconds)
    Path(args.output).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))
