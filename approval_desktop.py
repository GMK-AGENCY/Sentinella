"""Native accessibility adapter for the user's Sentinella application.

Inspection is the default. Invocation requires both an enabled configuration and
a recognizable native permission card, a file operation and delegated paths.
No screen-coordinate clicks, shell execution, CDP or global permission changes.
"""
import argparse
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re
import time

from approval_policy import ALWAYS, ONCE, DENY, normalize, is_inside, pick_button, button_key, file_change_decision
from question_desktop import walk, native_controls, desktop_is_unlocked, chat_content_available
from automation_policy import app_enabled, target_titles, action_block_reason, live_block_reason
from desktop_identity import app_window_handles

from runtime_paths import data_root
ROOT = data_root()


def invoke_record(record, app):
    if live_block_reason():
        return False
    from datetime import datetime
    path = ROOT / "logs" / "consensi.jsonl"
    path.parent.mkdir(exist_ok=True)
    key = record["fingerprint"]
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines()[-500:]:
            old = json.loads(line)
            if old.get("fingerprint") == key and time.time() - old.get("timestamp", 0) < 60:
                return False
    event = {"timestamp": time.time(), "time": datetime.now().astimezone().isoformat(), "app": app, "fingerprint": key, **record["summary"]}
    # Electron permission controls can acknowledge UIA Invoke without dispatching
    # the trusted input event. Focus the verified owner and click its live rect.
    import uiautomation as auto
    import psutil
    button = record["button"]
    expected_pid = button.ProcessId
    top = button.GetTopLevelControl()
    if top is None:
        raise RuntimeError("Finestra proprietaria non verificabile")
    expected_exe = psutil.Process(top.ProcessId).exe().casefold()
    top.SetActive(waitTime=0.1)
    if psutil.Process(auto.GetForegroundControl().ProcessId).exe().casefold() != expected_exe:
        button.SetFocus()
        auto.SwitchToThisWindow(top.NativeWindowHandle)
        time.sleep(0.1)
    if psutil.Process(auto.GetForegroundControl().ProcessId).exe().casefold() != expected_exe:
        fg = auto.GetForegroundControl()
        raise RuntimeError(f"Clic rinviato: finestra={top.Name}, attesa={expected_exe}, primo_piano={psutil.Process(fg.ProcessId).exe()}, titolo={fg.Name[:100]}")
    refreshed = inspect_dialog(record["control"], record["roots"], time.monotonic() + 2, record.get("prefer_always", False))
    if refreshed.get("fingerprint") != key:
        raise RuntimeError("La richiesta è cambiata dopo l'attivazione: clic annullato")
    button = refreshed["button"]
    rectangle = button.BoundingRectangle
    hit = auto.ControlFromPoint((rectangle.left + rectangle.right) // 2, (rectangle.top + rectangle.bottom) // 2)
    target_runtime = tuple(button.GetRuntimeId())
    for _ in range(10):
        if hit is None or tuple(hit.GetRuntimeId()) == target_runtime:
            break
        hit = hit.GetParentControl()
    if hit is None or tuple(hit.GetRuntimeId()) != target_runtime:
        raise RuntimeError("Pulsante coperto o ricreato dall'app: clic rinviato; "
            f"atteso={button.Name!r}, rect={rectangle}, "
            f"rilevato={getattr(hit, 'Name', None)!r}, tipo={getattr(hit, 'ControlTypeName', None)}, pid={getattr(hit, 'ProcessId', None)}")
    with path.open("a", encoding="utf-8") as out:
        out.write(json.dumps({**event, "status": "attempt"}, ensure_ascii=False) + "\n")
    if live_block_reason():
        return False
    button.Click(simulateMove=False, waitTime=0)
    with path.open("a", encoding="utf-8") as out:
        out.write(json.dumps({**event, "status": "invoked"}, ensure_ascii=False) + "\n")
    return True


def scan(config, app_name, inspect_only=False):
    import psutil
    import uiautomation as auto

    auto.SetGlobalSearchTimeout(1)
    blocked = "Sentinella in pausa globale" if config.get("paused") else None
    if not inspect_only:
        blocked = action_block_reason(config)
    if blocked:
        return {"app": app_name, "invoked": False, "reason": blocked}
    if not desktop_is_unlocked():
        return {"app": app_name, "invoked": False, "reason": "Desktop bloccato"}
    expected_path = config["apps"][app_name]["process_path"].casefold()
    windows = []
    roots = config["allowed_project_roots"]
    deadline = time.monotonic() + 6
    for handle in app_window_handles(expected_path):
        try:
            if config.get("simulation_window_handle") and handle != config["simulation_window_handle"]:
                continue
            windows.append(auto.ControlFromHandle(handle))
        except Exception:
            continue
    result = {"app": app_name, "windows": len(windows), "dialogs": [], "invoked": False, "enabled": config.get("enabled", False)}
    for window in windows:
        # Claude renders real tool approvals inline, without a dialog role.
        # Anchor on actual consent controls, then inspect their smallest card.
        controls = native_controls(window)
        if not config.get("simulation_window_handle") and not chat_content_available(controls):
            return {**result, "available": False, "reason": f"Interfaccia {app_name} temporaneamente non disponibile"}
        allowed_titles = target_titles(config, app_name, "approvals")
        if allowed_titles:
            titles = [c.Name.removesuffix(" - Claude Code") for c in controls if c.ControlTypeName == "DocumentControl" and c.Name]
            if not titles or titles[-1] not in allowed_titles:
                result["reason"] = "Chat fuori dalla delega attiva"
                continue
        result["consent_controls"] = [{"name": c.Name[:200], "type": c.ControlTypeName, "role": c.AriaRole} for c in controls if c.ControlTypeName in ("ButtonControl", "SplitButtonControl", "MenuItemControl") and ("consenti" in c.Name.casefold() or "nega" == c.Name.casefold())]
        seen = set()
        for anchor in controls:
            if anchor.ControlTypeName != "ButtonControl" or button_key(anchor.Name) not in ALWAYS | ONCE:
                continue
            parent = anchor.GetParentControl()
            for _ in range(5):
                if parent is None or time.monotonic() > deadline:
                    break
                key = tuple(parent.GetRuntimeId())
                if key in seen:
                    break
                seen.add(key)
                title = parent.Name.casefold()
                inline_permission = app_name == "claude" and title.startswith("richiesta di autorizzazione:")
                codex_card = app_name == "codex" and codex_permission_resources(walk(parent, limit=150, seconds=min(.3, max(0, deadline-time.monotonic())), max_depth=10)) is not None
                native_dialog = parent.AriaRole.casefold() in {"dialog", "alertdialog"}
                if not (inline_permission or codex_card or native_dialog or config.get("simulation_window_handle")):
                    parent = parent.GetParentControl()
                    continue
                record = inspect_dialog(parent, roots, deadline, config.get("prefer_always", False))
                if codex_card and not record.get("button") and not inspect_only and config.get("enabled") and app_enabled(config, app_name, "approvals"):
                    reveal_codex_resource(parent, deadline)
                    record = inspect_dialog(parent, roots, deadline, config.get("prefer_always", False))
                if record["summary"].get("buttons"):
                    result["dialogs"].append(record["summary"])
                if record.get("button"):
                    if not inspect_only and config.get("enabled") and app_enabled(config, app_name, "approvals"):
                        check = inspect_dialog(parent, roots, deadline, config.get("prefer_always", False))
                        if check.get("fingerprint") == record.get("fingerprint") and check.get("button") is not None:
                            target = check["button"]
                            label = target.Name
                            clicked = invoke_record(check, app_name)
                            result.update(invoked=clicked, button=label, fingerprint=record["fingerprint"])
                    return result
                parent = parent.GetParentControl()
        for control in controls:
            if time.monotonic() > deadline:
                result["truncated"] = True
                return result
            try:
                role = control.AriaRole.casefold()
                kind = control.ControlTypeName
                is_dialog = role in ("dialog", "alertdialog") or (config.get("simulation_window_handle") and kind == "WindowControl")
                if is_dialog and not control.IsOffscreen:
                    record = inspect_dialog(control, roots, deadline, config.get("prefer_always", False))
                    result["dialogs"].append(record["summary"])
                    candidate = record.get("button")
                    if candidate and not inspect_only and config.get("enabled") and app_enabled(config, app_name, "approvals"):
                        # Freshly verify the same native dialog before invoking its control.
                        check = inspect_dialog(control, roots, deadline, config.get("prefer_always", False))
                        if check.get("fingerprint") != record.get("fingerprint"):
                            result["reason"] = "La richiesta è cambiata prima del consenso"
                            return result
                        target = check["button"]
                        if target.IsEnabled and not target.IsOffscreen:
                            label = target.Name
                            clicked = invoke_record(check, app_name)
                            result.update(invoked=clicked, button=label, fingerprint=record["fingerprint"])
                            return result
            except Exception as exc:
                result["last_error"] = f"{type(exc).__name__}: {exc}"
    return result


def codex_permission_targets(controls):
    """Read resource labels from the native form, never from its model reason.

    Codex renders request_permissions inline. A basename alone does not prove
    a delegated folder, even if the model's explanation includes a full path.
    """
    if not any(getattr(c, "AriaRole", "").casefold() == "form" for c in controls):
        return None
    nodes = [c for c in controls if c.ControlTypeName == "TextControl" and c.Name.strip()]
    names = [c.Name.strip() for c in nodes]
    if not names or names[0] not in {"Autorizzazioni", "Permissions"}:
        return None
    prefixes = {"Consentire a ChatGPT di modificare i contenuti di", "Allow ChatGPT to edit the contents of"}
    if len(names) < 4 or names[1] not in prefixes or "?" not in names[2:]:
        return None
    return nodes[2:names.index("?", 2)]


def codex_permission_resources(controls):
    targets = codex_permission_targets(controls)
    return None if targets is None else [c.Name.strip() for c in targets]


def codex_resource_path(target):
    """Only the resource's own native description can expand its basename.

    The app attaches the full path to a tooltip on the resource label. Chromium
    exposes that resource description as FullDescription. It can be populated
    only after the guarded resource hover performed by reveal_codex_resource.
    Never search transcript text, the model's reason, or unrelated tooltips.
    """
    label = target.Name.strip()
    if PureWindowsPath(label).is_absolute():
        return label
    node = target
    for _ in range(5):
        if node is None or getattr(node, "AriaRole", "") in {"alert", "form", "dialog"}:
            break
        descriptions = []
        try:
            descriptions.append(node.HelpText)
        except Exception:
            pass
        try:
            descriptions.append(node.Element.GetCurrentPropertyValue(30159))
        except Exception:
            pass
        candidates = {value.strip() for value in descriptions if isinstance(value, str)
                      and PureWindowsPath(value.strip()).is_absolute()
                      and PureWindowsPath(value.strip()).name.casefold() == label.casefold()}
        if len(candidates) == 1:
            return candidates.pop()
        if len(candidates) > 1:
            return None
        try:
            node = node.GetParentControl()
        except Exception:
            break
    return None


def verified_hover_point(target, hit_test):
    """Wrapped text has a union rectangle whose centre can be empty space."""
    rect = target.BoundingRectangle
    if rect.right <= rect.left or rect.bottom <= rect.top:
        return None
    owners = set()
    node = target
    for _ in range(5):
        if node is None or getattr(node, "AriaRole", "") in {"alert", "form", "dialog"}:
            break
        owners.add(tuple(node.GetRuntimeId()))
        node = node.GetParentControl()
    xs = [(rect.left + rect.right)//2, rect.right-2, rect.left+2]
    ys = [(rect.top + rect.bottom)//2, rect.top+2, rect.bottom-2]
    for y in ys:
        for x in xs:
            if not rect.left <= x < rect.right or not rect.top <= y < rect.bottom:
                continue
            hit = hit_test(x, y)
            if hit is not None and tuple(hit.GetRuntimeId()) in owners:
                return x, y
    return None


def reveal_codex_resource(dialog, deadline):
    if live_block_reason() or time.monotonic()+1.3 > deadline:
        return False
    targets = codex_permission_targets(walk(dialog, limit=150, seconds=.3, max_depth=10))
    if not targets or len(targets) != 1 or codex_resource_path(targets[0]):
        return False
    import uiautomation as auto
    import psutil
    top = dialog.GetTopLevelControl()
    if top is None:
        return False
    expected = psutil.Process(top.ProcessId).exe().casefold()
    top.SetActive(waitTime=.1)
    if psutil.Process(auto.GetForegroundControl().ProcessId).exe().casefold() != expected or live_block_reason():
        return False
    target = targets[0]
    point = verified_hover_point(target, auto.ControlFromPoint)
    if point is None or live_block_reason():
        return False
    auto.SetCursorPos(*point)
    time.sleep(.8)
    return codex_resource_path(target) is not None


def inspect_dialog(dialog, roots, deadline, prefer_always=False):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return {"summary": {"eligible": False, "reason": "Timeout lettura dialogo"}}
    controls = [c for c in walk(dialog, limit=500, seconds=min(2, remaining), max_depth=20) if not c.IsOffscreen]
    labels = [c.Name for c in controls if c.ControlTypeName == "ButtonControl" and c.IsEnabled]
    text = "\n".join(c.Name for c in controls if c.ControlTypeName in ("TextControl", "EditControl", "DocumentControl", "WindowControl"))
    lowered = text.casefold()
    summary = {"title": dialog.Name[:200], "buttons": labels, "eligible": False, "card_text": text[:3500]}
    result = {"summary": summary}
    # Do not confuse an assistant's ordinary question with a native permission request.
    if not any(normalize(label) in DENY for label in labels) or not pick_button(labels):
        summary["reason"] = "Non è una coppia di controlli di consenso riconoscibile"
        return result
    targets = codex_permission_targets(controls)
    summary["file_request"] = targets is not None
    resources = None if targets is None else [codex_resource_path(c) for c in targets]
    if targets is not None:
        if not resources or not all(path and re.fullmatch(r"[A-Za-z]:[\\/].+", path) for path in resources):
            summary["waiting_for_resource"] = True
            summary["reason"] = "Codex mostra una richiesta file, ma il percorso completo della risorsa non è verificabile nelle cartelle delegate"
            return result
        if not all(is_inside(path, roots) for path in resources):
            summary.update(manual_required=True, reason="Percorso della risorsa fuori dalle cartelle delegate")
            return result
        # File permission comes from the app's resource field. The assistant's
        # reason below it cannot add or replace authorized paths.
        lowered = "write file\n" + "\n".join(resources).casefold()
    excluded = ("cdp", "capabilities.get", "emulation.", "network", "internet", "browser", "password", "login", "delete", "elimina", "cancella", "rimuovi", "exec_command", "bash", "shell", "cmd.exe", "terminal")
    if any(token in lowered + "\n" + text.casefold() for token in excluded):
        summary["reason"] = "Richiesta diversa dalla delega locale di lettura/scrittura file"
        return result
    if not any(token in lowered for token in ("apply_patch", "write file", "read file", "edit file", "scrivere", "leggere", "modificare file", "creare file")):
        summary["reason"] = "Operazione file non identificata"
        return result
    paths = resources if resources is not None else re.findall(r"[A-Za-z]:[\\/][^\r\n\"<>|]+", text)
    paths = [path.strip().rstrip("'` .") for path in paths]
    if not paths or not all(is_inside(path, roots) for path in paths):
        summary["reason"] = "Cartella delegata non verificabile nel dialogo"
        return result
    if resources is not None:
        decision = file_change_decision([{"path": path, "kind": "update"} for path in paths], roots, prefer_always=False)
        if decision.action != "accept":
            summary["reason"] = decision.reason
            return result
    # Persistent approval is used only when the dialog explicitly scopes it to the project/session.
    scope_text = lowered + "\n" + "\n".join(labels).casefold()
    persistent_scope = any(token in scope_text for token in ("in questo progetto", "per questa cartella", "per questo file", "for this project", "for this file", "this session", "questa sessione"))
    label = pick_button(labels, prefer_always=persistent_scope and prefer_always)
    if label is None:
        summary["reason"] = "Nessun consenso con la portata richiesta"
        return result
    selected = [c for c in controls if c.ControlTypeName == "ButtonControl" and c.Name == label]
    if len(selected) != 1:
        summary["reason"] = "Pulsante ambiguo"
        return result
    summary.update(eligible=True, selected=label, paths=paths)
    try:
        instance = list(dialog.GetRuntimeId())
    except AttributeError:
        instance = []
    # Consecutive requests for the same file are distinct approvals. Keep the
    # native card identity in the deduplication/freshness key, not just its text.
    result.update(button=selected[0], control=dialog, roots=roots, prefer_always=prefer_always, fingerprint=hashlib.sha256((text + json.dumps(labels) + json.dumps(paths) + json.dumps(instance)).encode()).hexdigest())
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", choices=("codex", "claude"), required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "approval-config.json")
    parser.add_argument("--inspect-only", action="store_true", help="Read native consent controls without clicking or changing focus")
    args = parser.parse_args()
    try:
        print(json.dumps(scan(json.loads(args.config.read_text(encoding="utf-8")), args.app, args.inspect_only), ensure_ascii=True), flush=True)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        print(json.dumps({"app": args.app, "error": f"{type(exc).__name__}: {exc}"}), flush=True)
        raise SystemExit(2)
