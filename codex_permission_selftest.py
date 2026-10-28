"""Explicit real Codex approval test, isolated from desktop input and live jobs."""
import argparse
import hashlib
import json
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time
import uuid

from approval_policy import file_change_decision
from bounded_process import stop_owned

EXPECTED = b"SENTINELLA_CODEX_PROTOCOL_CONSENT_OK\n"


def approval_response(request, changes, target, thread_id, turn_id, already_approved):
    """Accept exactly one add, for this run's one file and native request IDs."""
    params = request.get("params", {})
    proposed = changes.get(params.get("itemId"), [])
    valid = (request.get("method") == "item/fileChange/requestApproval"
             and thread_id and turn_id and params.get("threadId") == thread_id
             and params.get("turnId") == turn_id and not already_approved)
    exact = (len(proposed) == 1 and
             Path(proposed[0].get("path", "")).resolve() == target.resolve())
    policy = file_change_decision(proposed, [target.parent], params.get("grantRoot"), prefer_always=False)
    kind = proposed[0].get("kind") if proposed else None
    if isinstance(kind, dict):
        kind = kind.get("type")
    allowed = bool(valid and exact and kind == "add" and policy.action == "accept")
    return {"decision": "accept" if allowed else "cancel"}, policy.reason


def run(workspace, output, timeout=50):
    """Explicit opt-in; existing login required, no settings or saved chat changes."""
    if not 0 < timeout <= 50:
        raise ValueError("Self-test timeout must be within 0..50 seconds")
    workspace, output = Path(workspace).resolve(), Path(output).resolve()
    if not workspace.is_dir():
        raise ValueError("The test workspace must already exist")
    codex = shutil.which("codex")
    if not codex:
        raise ValueError("Codex CLI is not available; install/sign in to Codex separately")
    fixture = workspace / ("sentinella-consent-test-" + str(uuid.uuid4()))
    target = fixture / "consent-proof.txt"
    fixture.mkdir()
    output.mkdir(parents=True, exist_ok=False)
    report = {"fixture": str(fixture), "target": str(target), "success": False,
              "ephemeral": False, "approval_events": [], "file_events": []}
    events = queue.Queue()
    process = None
    thread_id = turn_id = None
    changes = {}
    started = time.time()
    deadline = time.monotonic() + timeout
    def save():
        (output / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    def send(value):
        process.stdin.write(json.dumps(value) + "\n")
        process.stdin.flush()
    with (output / "stderr.log").open("w", encoding="utf-8") as errors:
        try:
            process = subprocess.Popen([codex, "app-server"], cwd=fixture, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=errors, text=True, encoding="utf-8", bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            def receive():
                for line in process.stdout:
                    try:
                        events.put(json.loads(line))
                    except ValueError:
                        pass
            threading.Thread(target=receive, daemon=True).start()
            send({"id": 1, "method": "initialize", "params": {
                "clientInfo": {"name": "sentinella_permission_selftest", "version": "0.1.2"},
                "capabilities": {"experimentalApi": True}}})
            while time.monotonic() < deadline:
                try:
                    message = events.get(timeout=.25)
                except queue.Empty:
                    if process.poll() is not None:
                        raise RuntimeError("Codex app-server exited")
                    continue
                method, params = message.get("method"), message.get("params", {})
                if "error" in message:
                    raise RuntimeError(str(message["error"]))
                if method and "id" in message:
                    if method != "item/fileChange/requestApproval":
                        report.setdefault("unexpected_requests", []).append(method)
                        send({"id": message["id"], "error": {"code": -32601, "message": "Outside this file-consent test"}})
                        raise RuntimeError("Unexpected approval request: " + method)
                    response, reason = approval_response(message, changes, target, thread_id, turn_id,
                        any(e["response"]["decision"] == "accept" for e in report["approval_events"]))
                    report["approval_events"].append({"request": message, "response": response, "policy_reason": reason})
                    send({"id": message["id"], "result": response})
                    save()
                    continue
                if message.get("id") == 1:
                    send({"method": "initialized"})
                    send({"id": 2, "method": "thread/start", "params": {"cwd": str(fixture), "ephemeral": True,
                        "sandbox": "read-only", "approvalPolicy": "on-request", "approvalsReviewer": "user"}})
                elif message.get("id") == 2:
                    thread = message["result"]["thread"]
                    if not thread.get("ephemeral"):
                        raise RuntimeError("Codex did not create an ephemeral test session")
                    thread_id = thread["id"]
                    report.update(thread_id=thread_id, ephemeral=True)
                    prompt = ("Isolated Sentinella integration test explicitly requested by the user. "
                        "Use ONLY apply_patch to add " + str(target) + " with exactly " + EXPECTED.decode().strip() +
                        " and one final newline. The read-only sandbox requires file-change approval; wait for the client. "
                        "No shell, extra permissions, network, delegation, other files, or retries. After the patch result, stop.")
                    send({"id": 3, "method": "turn/start", "params": {"threadId": thread_id,
                        "input": [{"type": "text", "text": prompt}]}})
                elif message.get("id") == 3:
                    turn_id = message["result"]["turn"]["id"]
                    report["turn_id"] = turn_id
                if method == "turn/started":
                    turn_id = params["turn"]["id"]
                    report["turn_id"] = turn_id
                if method in {"item/started", "item/completed"}:
                    item = params.get("item", {})
                    if item.get("type") == "fileChange":
                        changes[item["id"]] = item.get("changes", [])
                        report["file_events"].append(message)
                        save()
                if method == "serverRequest/resolved":
                    report.setdefault("resolved_requests", []).append(message)
                if method == "turn/completed":
                    report["turn_result"] = params["turn"].get("status")
                    break
            else:
                report["timed_out"] = True
            report["file_exists"] = target.exists()
            if target.exists():
                content = target.read_bytes()
                report.update(file_sha256=hashlib.sha256(content).hexdigest(), content_matches=content == EXPECTED,
                              file_mtime=target.stat().st_mtime)
            completed = any(e["params"]["item"].get("status") == "completed" for e in report["file_events"]
                            if e["method"] == "item/completed")
            report["success"] = bool(len(report["approval_events"]) == 1 and
                report["approval_events"][0]["response"]["decision"] == "accept" and completed and
                report.get("content_matches") and report.get("file_mtime", 0) >= started and
                report.get("turn_result") == "completed" and not report.get("unexpected_requests"))
        except Exception as error:
            report["error"] = str(error)
        finally:
            if process is not None:
                if thread_id and turn_id and not report.get("turn_result"):
                    try:
                        send({"id": 99, "method": "turn/interrupt", "params": {"threadId": thread_id, "turnId": turn_id}})
                    except (OSError, ValueError):
                        pass
                stop_owned(process, {})
                for stream in (process.stdin, process.stdout):
                    if stream:
                        stream.close()
            report["finished_at"] = time.time()
            save()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Real opt-in Codex consent/write test; consumes one model turn.")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New private report directory")
    args = parser.parse_args(argv)
    report = run(args.workspace, args.output)
    print(json.dumps({"success": report["success"], "report": str(args.output.resolve() / "report.json")}))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
