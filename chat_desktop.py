"""Bounded native adapter for user-selected periodic chat messages."""
import argparse
import json
from collections import Counter
from approval_policy import normalize
from automation_policy import action_block_reason, live_block_reason, idle_seconds, typing_keys_down
from question_desktop import walk, native_controls, desktop_is_unlocked, question_card, chat_content_available
from desktop_identity import app_window_handles

from runtime_paths import data_root
ROOT = data_root()
SEND = {"invia", "invia messaggio", "invia prompt", "send", "send message", "submit",
        "reindirizza", "steer", "invia ora", "send now"}


def snapshot(config, app):
    import psutil
    import uiautomation as auto
    auto.SetGlobalSearchTimeout(1)
    if not desktop_is_unlocked():
        raise RuntimeError("Desktop bloccato o non interattivo")
    expected = config["apps"][app]["process_path"].casefold()
    windows = [auto.ControlFromHandle(handle) for handle in app_window_handles(expected)]
    if len(windows) != 1:
        raise RuntimeError(f"Finestra {app} non univoca o non aperta ({len(windows)})")
    controls = native_controls(windows[0])
    if not chat_content_available(controls):
        raise RuntimeError(f"Interfaccia {app} temporaneamente non disponibile")
    docs = [c for c in controls if c.ControlTypeName == "DocumentControl" and c.Name]
    title = docs[-1].Name.removesuffix(" - Claude Code") if docs else ""
    return controls, title


def entries(controls, app):
    buttons = [c for c in controls if c.ControlTypeName == "ButtonControl"]
    found = []
    if app == "codex":
        source, row, in_row = "Recenti", None, False
        status = []
        for control in controls:
            if control.ControlTypeName == "ListControl":
                source = control.Name or "Recenti"
                row, in_row, status = None, False, []
            elif control.ControlTypeName == "ListItemControl":
                row, in_row, status = None, True, []
            elif control.ControlTypeName == "ButtonControl" and in_row:
                if control.Name in {"Archivia chat", "Archive chat"} and row is not None:
                    found.append(({"app": app, "title": row.Name, "group": source, "selector": row.Name, "status": ", ".join(status) or "Disponibile"}, row))
                    row, in_row = None, False
                elif row is None:
                    row = control
            elif control.AriaRole == "status" and control.Name and in_row:
                status.append(control.Name)
        controls = []
    options = {c.Name.removeprefix("Altre opzioni per ") for c in buttons if c.Name.startswith("Altre opzioni per ")}
    group = ""
    for c in controls:
        if app == "claude" and c.AriaRole == "heading" and any(b.Name == c.Name for b in buttons):
            group = c.Name
        if c.ControlTypeName != "ButtonControl" or not c.Name:
            continue
        title = None
        local = []
        if app == "codex":
            # These rows were found through their own native Archive control;
            # no scan through the transcript's hundreds of message buttons.
            title = c.Name
            parent = c.GetParentControl()
            source = "Recenti"
            for _ in range(4):
                if parent is None:
                    break
                if parent.ControlTypeName == "ListControl":
                    source = parent.Name or "Recenti"
                    break
                parent = parent.GetParentControl()
        else:
            suffixes = (" Avviata da ", " Bozza", " Unito", " Cloud", " In esecuzione", " Running", " Working",
                        " Risposta non letta", " Unread response", " Ha fatto una domanda")
            matches = [n for n in options if c.Name == n
                       or any(c.Name.startswith(n + suffix) for suffix in suffixes)
                       or c.Name.startswith('Bloccata ' + n + ' Ha fatto una domanda')]
            if not matches:
                continue
            title = max(matches, key=len)
            source = group or "Sessioni"
            local = walk(c, limit=20, seconds=0.15, max_depth=4)
        status = ", ".join(dict.fromkeys(n.Name for n in local if (n.ControlTypeName == "StatusControl" or n.AriaRole == "status") and n.Name)) or "Disponibile"
        if app == "claude" and any(c.Name.startswith(title + suffix) for suffix in (" In esecuzione", " Running", " Working")):
            status = "In esecuzione"
        found.append(({"app": app, "title": title, "group": source, "selector": c.Name, "status": status}, c))
    # Repeated Codex rows in project and Recents represent the same chat.
    counts = Counter((r["title"], r["group"]) for r, _ in found)
    groups = {}
    for r, _ in found:
        groups.setdefault(r["title"], set()).add(r["group"])
    result = []
    seen = set()
    for row, control in found:
        non_recent = groups[row["title"]] - {"Recenti"}
        if app == "codex" and row["group"] == "Recenti" and non_recent:
            continue
        key = row["title"], row["group"]
        if key in seen:
            continue
        seen.add(key)
        row["ambiguous"] = counts[key] > 1 or len(non_recent) > 1
        result.append((row, control))
    return result


def inventory(config, app):
    controls, current = snapshot(config, app)
    from chat_catalog import enrich
    rows, warning = enrich([r for r, _ in entries(controls, app)], app)
    return {"app": app, "current_title": current, "chats": rows, "metadata_warning": warning}


def timer_target(rows, job):
    """Pinning moves an existing unique chat between virtual sidebar lists."""
    titled = [(row, control) for row, control in rows if row["title"] == job["title"]]
    scoped = [(row, control) for row, control in titled if row["group"] == job["group"]]
    if scoped:
        return scoped
    virtual = {"Recenti", "Recents", "Fissati", "Pinned"}
    if job["app"] == "codex" and job["group"] in virtual and len(titled) == 1:
        row, _ = titled[0]
        if row["group"] in virtual and not row["ambiguous"]:
            return titled
    return []


def select_chat(config, app, title):
    blocked = action_block_reason(config)
    if blocked:
        return {"app": app, "action": "none", "reason": blocked}
    controls, current = snapshot(config, app)
    choices = [(row, c) for row, c in entries(controls, app) if row["title"] == title and not row["ambiguous"]]
    if len(choices) != 1:
        return {"app": app, "action": "none", "reason": "Chat non univoca o non disponibile"}
    if current != title:
        blocked = live_block_reason()
        if blocked:
            return {"app": app, "action": "none", "reason": blocked}
        choices[0][1].GetInvokePattern().Invoke()
        return {"app": app, "action": "selected_chat"}
    return {"app": app, "action": "none", "current_title": current}


def step(config, job, delivery, live_guard=None):
    app = job["app"]
    live_guard = live_guard or live_block_reason
    blocked = action_block_reason(config)
    if blocked:
        return {"app": app, "action": "none", "reason": blocked}
    controls, current = snapshot(config, app)
    result = {"app": app, "title": job["title"], "action": "none"}
    matches = timer_target(entries(controls, app), job)
    if len(matches) != 1 or matches[0][0]["ambiguous"]:
        return {**result, "reason": "Chat non trovata o titolo ambiguo: aggiorna la selezione"}
    busy_states = {"in elaborazione", "in esecuzione", "running", "working"}
    if not job.get("send_when_active") and any(part.strip().casefold() in busy_states for part in matches[0][0].get("status", "").split(",")):
        return {**result, "reason": "Chat in elaborazione: invio rinviato senza cambiare chat"}
    blocked = live_guard()
    if blocked:
        return {**result, "reason": blocked}
    if current != job["title"]:
        if config.get("native_background_delivery"):
            return {**result, "reason": "Cambio chat rinviato durante l'uso del PC"}
        matches[0][1].GetInvokePattern().Invoke()
        return {**result, "action": "selected_chat"}
    buttons = [c for c in controls if c.ControlTypeName == "ButtonControl"]
    if not job.get("send_when_active") and any(normalize(b.Name) in {"interrompi", "stop", "stop response", "interrompi risposta"} for b in buttons):
        return {**result, "reason": "Chat in elaborazione: invio rinviato"}
    if question_card(controls) or any(c.AriaRole in {"dialog", "alertdialog"} for c in controls):
        return {**result, "reason": "Domanda o dialogo aperto: invio rinviato"}
    labels = {"Prompt"} if app == "claude" else {"Lavora con Codex", "Ask Codex", "Message Codex"}
    edits = [c for c in controls if c.ControlTypeName == "EditControl" and c.Name in labels and c.IsEnabled]
    if len(edits) != 1:
        return {**result, "reason": "Campo messaggio non univoco"}
    edit = edits[0]
    if job.get('send_when_present') and getattr(edit, 'HasKeyboardFocus', False) is True and typing_keys_down():
        return {**result, 'reason': 'Digitazione nel campo messaggio: attendo il rilascio della tastiera'}
    value = edit.GetValuePattern().Value
    if not value.strip() or normalize(value) == normalize(edit.Name):
        value = ""
    message = job["message"]
    if delivery.get('cleanup'):
        if not value:
            return {**result, 'action': 'draft_cleared'}
        if value != message:
            return {**result, 'action': 'draft_preserved', 'reason': 'Bozza modificata: conservata integralmente nel campo'}
        # The caller proves ownership and receipt, then fsyncs the exact draft
        # before allowing this one native cleanup. A changed draft is untouched.
        delivery['archive'](value)
        blocked = live_guard()
        if blocked:
            return {**result, 'reason': blocked}
        if edit.GetValuePattern().Value != value:
            return {**result, 'action': 'draft_preserved', 'reason': 'Bozza cambiata durante il controllo: conservata'}
        idle_before = idle_seconds()
        edit.GetValuePattern().SetValue('', waitTime=0)
        if edit.GetValuePattern().Value.strip():
            return {**result, 'reason': 'Ricevuta verificata e bozza salvata; attendo la pulizia del campo'}
        return {**result, 'action': 'draft_cleared', 'idle_before': idle_before, 'idle_after': idle_seconds()}
    if value in job.get("legacy_messages", []) and delivery.get("drafted"):
        blocked = live_guard()
        if blocked:
            return {**result, "reason": blocked}
        edit.GetValuePattern().SetValue(message, waitTime=0)
        return {**result, "action": "drafted"}
    if value and (value != message or not delivery.get("drafted")):
        return {**result, "reason": "Bozza presente: non viene sovrascritta né inviata"}
    if not value:
        if delivery.get("drafted") and not delivery.get('can_redraft'):
            return {**result, "action": "uncertain", "reason": "Bozza scomparsa: verifica manuale prima di ripetere"}
        blocked = live_guard()
        if blocked:
            return {**result, "reason": blocked}
        if delivery.get('own_draft'):
            delivery['own_draft']()
        fresh_value = edit.GetValuePattern().Value
        if fresh_value.strip() and normalize(fresh_value) != normalize(edit.Name):
            return {**result, 'action': 'draft_preserved', 'reason': 'Bozza apparsa durante la preparazione: conservata'}
        idle_before = idle_seconds()
        edit.GetValuePattern().SetValue(message, waitTime=0)
        return {**result, "action": "drafted", 'idle_before': idle_before, 'idle_after': idle_seconds()}
    # The action is only the observed Send button beside this composer.
    parent = edit.GetParentControl()
    for _ in range(5):
        if parent is None:
            break
        local = walk(parent, limit=130, seconds=0.5, max_depth=9)
        sends = [c for c in local if c.ControlTypeName == "ButtonControl" and normalize(c.Name) in SEND and c.IsEnabled]
        if len(sends) == 1:
            return {**result, "action": "ready", "button": sends[0],
                    'validate_draft': lambda: edit.GetValuePattern().Value == message}
        if len(local) >= 130:
            break
        parent = parent.GetParentControl()
    return {**result, "reason": "Pulsante Invia non disponibile"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", choices=("codex", "claude"), required=True)
    parser.add_argument("--select-title")
    args = parser.parse_args()
    try:
        config = json.loads((ROOT / "approval-config.json").read_text(encoding="utf-8"))
        result = select_chat(config, args.app, args.select_title) if args.select_title else inventory(config, args.app)
        print(json.dumps(result, ensure_ascii=True), flush=True)
    except Exception as exc:
        print(json.dumps({"app": args.app, "error": str(exc)}), flush=True)
        raise SystemExit(2)
