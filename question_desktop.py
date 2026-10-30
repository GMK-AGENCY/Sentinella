"""Sentinella's native question-card adapter, including background chat selection.

Only the fixed, user-delegated absence reply can be sent. Consent dialogs are
never answered by this adapter. Each invocation performs at most one UI step.
"""
import argparse
import hashlib
import json
import time

from approval_policy import normalize
from automation_policy import app_enabled, target_titles, action_block_reason, live_block_reason
from desktop_identity import app_window_handles, foreground_process_id, native_document, document_controls

from runtime_paths import data_root
ROOT = data_root()


def desktop_is_unlocked():
    """Never send through a locked or secure Windows desktop."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    user32.OpenInputDesktop.restype = wintypes.HANDLE
    user32.GetUserObjectInformationW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    user32.CloseDesktop.argtypes = [wintypes.HANDLE]
    handle = user32.OpenInputDesktop(0, False, 1)
    if not handle:
        return False
    try:
        name = ctypes.create_unicode_buffer(256)
        length = wintypes.DWORD()
        return bool(user32.GetUserObjectInformationW(handle, 2, name, ctypes.sizeof(name), ctypes.byref(length))) and name.value.casefold() == "default"
    finally:
        user32.CloseDesktop(handle)


class ObservedControl:
    """Cache read-only labels for this one scan, while actions remain native."""
    def __init__(self, control):
        self.control = control
        self.Name = control.Name
        self.ControlTypeName = control.ControlTypeName
        try:
            self.AriaRole = control.AriaRole or ""
        except Exception:
            self.AriaRole = ""

    def __getattr__(self, name):
        return getattr(self.control, name)


def chat_content_available(controls):
    # A window can expose only its three title-bar buttons while its web
    # content is unavailable. Those buttons do not constitute a chat scan.
    return any(c.ControlTypeName in {"DocumentControl", "EditControl", "ListControl", "ListItemControl"} for c in controls)


def native_controls(window, *, include_inputs=False):
    """One native, cached query instead of thousands of cross-process reads.

    The whole transcript is intentionally absent. A question's text is read
    only from its actual local card/message, after locating the relevant input.
    """
    import uiautomation as auto
    from uiautomation import uiautomation as implementation
    client = implementation._AutomationClient.instance().IUIAutomation
    cache = client.CreateCacheRequest()
    for property_id in (30003, 30005, 30101):
        cache.AddProperty(property_id)
    cache.TreeScope = 1
    kinds = [auto.ControlType.ButtonControl, auto.ControlType.EditControl, auto.ControlType.DocumentControl,
             auto.ControlType.WindowControl, auto.ControlType.ListControl, auto.ControlType.ListItemControl]
    if include_inputs:
        kinds += [auto.ControlType.CheckBoxControl, auto.ControlType.ComboBoxControl, auto.ControlType.RadioButtonControl,
                  auto.ControlType.SliderControl, auto.ControlType.SpinnerControl, auto.ControlType.MenuItemControl,
                  auto.ControlType.TabItemControl, auto.ControlType.TreeItemControl, auto.ControlType.DataGridControl]
    conditions = [client.CreatePropertyCondition(30003, kind) for kind in kinds]
    conditions += [client.CreatePropertyCondition(30101, role) for role in ("heading", "status", "dialog", "alertdialog")]
    for name in ("Richiede input", "In attesa di input", "Requires input", "Needs input"):
        conditions.append(client.CreatePropertyCondition(30005, name))
    condition = conditions[0]
    for extra in conditions[1:]:
        condition = client.CreateOrCondition(condition, extra)
    elements = window.Element.FindAllBuildCache(4, condition, cache)
    result = []
    for index in range(min(elements.Length, 2500)):
        try:
            element = elements.GetElement(index)
            constructor = auto.ControlConstructors[element.CachedControlType]
            observed = ObservedControl.__new__(ObservedControl)
            observed.control = constructor(element=element)
            observed.Name = element.CachedName or ""
            observed.ControlTypeName = constructor.__name__
            role = element.GetCachedPropertyValue(30101)
            observed.AriaRole = role if isinstance(role, str) else ""
            result.append(observed)
        except Exception:
            continue
    return result


def walk(root, limit=1200, seconds=3, max_depth=70):
    deadline = time.monotonic() + seconds
    stack = [(root, 0, (), False)]
    result = []
    while stack and len(result) < limit and time.monotonic() < deadline:
        control, depth, path, in_main = stack.pop()
        try:
            control = ObservedControl(control)
            result.append((path, control))
            in_main = in_main or control.AriaRole == "main"
            if depth < max_depth:
                # Read the composer/latest messages before older history. Each
                # sibling iteration is bounded; GetChildren builds an unbounded
                # list first and can stall on very long conversations.
                children = []
                child = control.GetLastChildControl() if in_main else control.GetFirstChildControl()
                cap = 80 if in_main else 240
                while child is not None and len(children) < cap and time.monotonic() < deadline:
                    ordinal = -len(children) - 1 if in_main else len(children)
                    children.append((child, depth + 1, path + (ordinal,), in_main))
                    child = child.GetPreviousSiblingControl() if in_main else child.GetNextSiblingControl()
                stack.extend(reversed(children))
        except Exception:
            continue
    return [control for _, control in sorted(result, key=lambda row: row[0])]


def question_card(controls):
    for edit in controls:
        try:
            if edit.ControlTypeName != "EditControl":
                continue
            if edit.IsOffscreen is True or edit.IsEnabled is False:
                continue
            label = normalize(edit.Name)
            if not (label.startswith("rispondi") or label.startswith("oppure scrivi una risposta personalizzata") or label.startswith("reply") or label.startswith("type your answer")):
                continue
            parent = edit.GetParentControl()
            for _ in range(7):
                if parent is None:
                    break
                local = walk(parent, limit=100, seconds=1, max_depth=10)
                if len(local) >= 100:
                    break
                # Empty question cards disable Send until an answer is typed.
                # Recognize their structure now; inspect() checks IsEnabled
                # again before invoking the freshly observed Send control.
                buttons = [c for c in local if c.ControlTypeName == "ButtonControl"
                           and c.IsOffscreen is not True]
                labels = {normalize(c.Name) for c in buttons}
                edit_nodes = {tuple(c.GetRuntimeId()) for c in walk(edit, limit=30, seconds=0.5, max_depth=6)}
                names = [c.Name for c in local if c.ControlTypeName == "TextControl" and c.Name and tuple(c.GetRuntimeId()) not in edit_nodes]
                if labels.intersection({"salta", "skip"}) and labels.intersection({"invia", "send", "submit"}) and any(normalize(n) in {"domanda", "question"} for n in names):
                    send = [c for c in buttons if normalize(c.Name) in {"invia", "send", "submit"}]
                    text = "\n".join(n for n in names if normalize(n) not in {"domanda", "question", normalize(edit.Name)} and n.strip())
                    if len(send) == 1 and text:
                        return {"edit": edit, "send": send[0], "text": text, "runtime": list(parent.GetRuntimeId())}
                parent = parent.GetParentControl()
        except Exception:
            continue
    return None


def plain_claude_candidate(controls, title, allowed_titles, away_reply=""):
    """Only the latest, unanswered assistant message in the selected chat."""
    if not title or (allowed_titles and title not in allowed_titles):
        return False
    from approval_policy import DENY, ONCE, ALWAYS, button_key
    labels = {button_key(c.Name) for c in controls if c.ControlTypeName == "ButtonControl" and not getattr(c, "IsOffscreen", False)}
    if labels & DENY and labels & (ONCE | ALWAYS):
        return False
    if any(c.ControlTypeName == "ButtonControl" and normalize(c.Name) in {"interrompi", "stop", "stop response", "stop generating"} for c in controls):
        return False
    authors = [c.Name for c in controls if c.AriaRole == "heading" and c.Name.startswith(("Claude ha risposto:", "Hai detto:", "Claude said:", "You said:"))]
    user_heads = [name for name in authors if name.startswith(("Hai detto:", "You said:"))]
    if away_reply and user_heads:
        # An acknowledgement may quote the deferred question. It is not a
        # fresh human request and must not restart an absence-reply loop.
        prefix = normalize(away_reply.split(".", 1)[0])
        if prefix and normalize(user_heads[-1].split(":", 1)[-1]).startswith(prefix):
            return False
    return bool(authors and authors[-1].startswith(("Claude ha risposto:", "Claude said:")))


def inspect(config, app_name, target_title=None, act=False):
    import psutil
    import uiautomation as auto
    auto.SetGlobalSearchTimeout(1)
    if act:
        blocked = action_block_reason(config)
        if blocked:
            return {"app": app_name, "action": "none", "reason": blocked}
    if not desktop_is_unlocked():
        return {"app": app_name, "action": "none", "reason": "Desktop bloccato o non interattivo"}
    allowed_titles = target_titles(config, app_name, "questions")
    if act and not app_enabled(config, app_name, "questions"):
        return {"app": app_name, "action": "none", "reason": "Applicazione disattivata"}
    if target_title and allowed_titles and target_title not in allowed_titles:
        return {"app": app_name, "action": "none", "reason": "Chat esclusa dal controllo delle domande"}
    expected = config["apps"][app_name]["process_path"].casefold()
    windows = [auto.ControlFromHandle(handle) for handle in app_window_handles(expected)]
    result = {"app": app_name, "windows": len(windows), "action": "none", "away_mode": config.get("away_mode", False)}
    try:
        result["foreground_process"] = psutil.Process(foreground_process_id()).name()
    except psutil.Error:
        result["foreground_process"] = "unknown"
    result['usable_windows'] = 0
    for window in windows:
        try:
            controls = native_controls(window)
        except Exception as exc:
            result.update(partial=True, partial_reason=f'Una finestra non è leggibile: {type(exc).__name__}')
            continue
        if not chat_content_available(controls):
            continue
        result['usable_windows'] += 1
        buttons = [c for c in controls if c.ControlTypeName == "ButtonControl"]
        edits = [c for c in controls if c.ControlTypeName == "EditControl"]
        result["documents"] = [c.Name[:150] for c in controls if c.ControlTypeName == "DocumentControl"]
        result["current_title"] = next((name.removesuffix(" - Claude Code") for name in reversed(result["documents"]) if name), "")
        if app_name == "claude":
            from chat_desktop import document_sessions
            native = document_sessions(controls)
            if native:
                result["current_title"] = native[-1][0].Name.removesuffix(" - Claude Code")
        result["headings"] = [c.Name[:200] for c in controls if c.AriaRole == "heading" or c.ControlTypeName == "HeaderControl"]
        result["edit_labels"] = [c.Name[:150] for c in edits]
        result["question_buttons"] = [c.Name for c in buttons if normalize(c.Name) in {"rispondi alla domanda", "answer question", "answer the question"}]
        result["question_controls"] = [{"label": c.Name, "offscreen": c.IsOffscreen, "enabled": c.IsEnabled} for c in buttons if normalize(c.Name) in {"rispondi alla domanda", "answer question", "answer the question"}]
        pending = []
        for c in controls:
            try:
                if normalize(c.Name) not in {"richiede input", "needs input", "requires input", "in attesa di input", "ha fatto una domanda"}:
                    continue
                parent = c.GetParentControl()
                for _ in range(6):
                    if parent is None:
                        break
                    if parent.ControlTypeName == "ButtonControl" and parent.Name:
                        pending.append(parent.Name.split(" Avviata da ")[0])
                        break
                    parent = parent.GetParentControl()
            except Exception:
                continue
        result["pending_chats"] = list(dict.fromkeys(pending))
        if app_name == "claude":
            result["blocked_chat_buttons"] = [c.Name for c in buttons if c.Name.startswith("Bloccata ") and "Ha fatto una domanda" in c.Name]
            result["pending_chats"].extend(name.removeprefix("Bloccata ").split(" Ha fatto una domanda")[0] for name in result["blocked_chat_buttons"])
        # A target supplied by a trusted notification is selected through the native sidebar.
        if target_title and target_title not in result["documents"] + result["headings"]:
            matches = [c for c in buttons if c.IsEnabled and (c.Name == target_title or (app_name == "claude" and (c.Name.startswith(target_title + " Avviata da ") or c.Name.startswith("Bloccata " + target_title + " Ha fatto una domanda"))))]
            if matches and act and config.get("away_mode"):
                blocked = live_block_reason()
                if blocked:
                    return {**result, "reason": blocked}
                matches[0].GetInvokePattern().Invoke()
                result.update(action="selected_chat", target=target_title)
                return result
            result["reason"] = "Chat richiesta non aperta: nessuna risposta inviata"
            continue
        documents=[c for c in controls if c.ControlTypeName=='DocumentControl' and native_document(c,app_name) is not None]
        if target_title:
            documents=[c for c in documents if c.Name.removesuffix(' - Claude Code')==target_title] if app_name=='claude' else documents
        if len(documents)!=1:
            result['reason']='Documento della chat non univoco o non disponibile'
            continue
        controls=document_controls(controls,documents[0])
        buttons=[c for c in controls if c.ControlTypeName=='ButtonControl']
        edits=[c for c in controls if c.ControlTypeName=='EditControl']
        card = question_card(controls)
        plain_title = target_title or result["current_title"]
        if card is None and app_name == "claude" and plain_title in result["headings"] and plain_claude_candidate(controls, plain_title, allowed_titles, config.get("away_reply", "")):
            prompts = [c for c in edits if c.Name == "Prompt" and c.IsEnabled]
            assistant_heads = [c for c in controls if c.AriaRole == "heading" and c.Name.startswith("Claude ha risposto:")]
            if len(prompts) == 1 and assistant_heads:
                # Claude can mark an ordinary final message as awaiting input without a question card.
                prompt_nodes = {tuple(c.GetRuntimeId()) for c in walk(prompts[0], limit=30, seconds=0.5, max_depth=6)}
                message_controls = walk(assistant_heads[-1].GetParentControl(), limit=350, seconds=1.5)
                text_nodes = [c.Name for c in message_controls if c.ControlTypeName == "TextControl" and c.Name.strip() and tuple(c.GetRuntimeId()) not in prompt_nodes]
                text = "\n".join(text_nodes)[-6000:] or assistant_heads[-1].Name
                sends = [c for c in buttons if normalize(c.Name) in {"invia", "invia messaggio", "send", "send message", "submit"}]
                tail = text[-2200:].casefold()
                dated = any(token in tail for token in ("24 ore fa", "giorni fa", "ieri", "yesterday"))
                stopped = any(token in tail for token in ("my ferma", "non ho preso altre issue", "lavoro trasferito", "sessione chiusa"))
                if not dated and not stopped and ("?" in tail or any(token in tail for token in ("decisione per ", "attendo una risposta", "mi confermi", "fammi sapere", "in attesa della tua risposta"))):
                    card = {"edit": prompts[0], "send": sends[0] if len(sends) == 1 else None, "text": text, "runtime": list(assistant_heads[-1].GetRuntimeId()), "plain_claude": True}
                    result["pending_chats"] = list(dict.fromkeys([*result["pending_chats"], plain_title]))
        if card:
            result.update(question=card["text"], runtime=card["runtime"])
            if not act or not config.get("away_mode"):
                return result
            if card.get("plain_claude"):
                history_path = ROOT / "logs" / "domande-rinviate.jsonl"
                if history_path.exists():
                    from datetime import datetime
                    for line in history_path.read_text(encoding="utf-8").splitlines():
                        previous = json.loads(line)
                        if previous.get("status") == "sent" and previous.get("app") == app_name and previous.get("thread") == (target_title or result["current_title"]):
                            age = (datetime.now().astimezone() - datetime.fromisoformat(previous["sent_at"])).total_seconds()
                            if age < 300:
                                result["reason"] = "Rinvio già inviato a questa chat negli ultimi 5 minuti"
                                return result
            reply = config["away_reply"]
            value = card["edit"].GetValuePattern().Value
            # Codex exposes its empty contenteditable placeholder as a UIA value.
            if not value.strip() or normalize(value) == normalize(card["edit"].Name):
                value = ""
            from message_templates import reply_is_replaceable
            if not reply_is_replaceable(value, config):
                result["reason"] = "Campo già compilato: non sovrascrivo una risposta dell'utente"
                return result
            if value != reply:
                blocked = live_block_reason()
                if blocked:
                    return {**result, "reason": blocked}
                card["edit"].GetValuePattern().SetValue(reply, waitTime=0)
                result["action"] = "drafted_reply"
                return result
            if card["send"] is not None and card["send"].IsEnabled:
                blocked = live_block_reason()
                if blocked:
                    return {**result, "reason": blocked}
                key = hashlib.sha256((app_name + json.dumps(card["runtime"]) + card["text"]).encode()).hexdigest()
                LOGS = ROOT / "logs"
                LOGS.mkdir(exist_ok=True)
                history = LOGS / "domande-rinviate.jsonl"
                if history.exists() and key in history.read_text(encoding="utf-8"):
                    result["reason"] = "Risposta già inviata a questa scheda"
                    return result
                from datetime import datetime
                now = datetime.now().astimezone()
                record = {"id": key, "app": app_name, "thread": target_title or result["current_title"], "question": card["text"], "reply": reply, "sent_at": now.isoformat(), "review_when": "user_returns_and_messages"}
                # Append an attempt before the click so a crash cannot cause duplicate sends.
                with history.open("a", encoding="utf-8") as out:
                    out.write(json.dumps({**record, "status": "attempt"}, ensure_ascii=False) + "\n")
                card["send"].GetInvokePattern().Invoke()
                with history.open("a", encoding="utf-8") as out:
                    out.write(json.dumps({**record, "status": "sent"}, ensure_ascii=False) + "\n")
                result.update(action="sent_reply", reply=reply)
                return result
        # Open an actual question card; text in ordinary chat messages is never used as an answer.
        opens = [c for c in buttons if normalize(c.Name) in {"rispondi alla domanda", "answer question", "answer the question"} and c.IsEnabled]
        if opens and act and config.get("away_mode"):
            blocked = live_block_reason()
            if blocked:
                return {**result, "reason": blocked}
            opens[-1].GetInvokePattern().Invoke()
            result["action"] = "opened_question"
            return result
    if windows and not result['usable_windows']:
        result.update(available=False, reason=f'Interfaccia {app_name} temporaneamente non disponibile')
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--app", choices=("codex", "claude"), required=True)
    p.add_argument("--target-title")
    p.add_argument("--act", action="store_true")
    args = p.parse_args()
    try:
        print(json.dumps(inspect(json.loads((ROOT / "approval-config.json").read_text(encoding="utf-8")), args.app, args.target_title, args.act), ensure_ascii=True), flush=True)
    except Exception as exc:
        print(json.dumps({"app": args.app, "error": f"{type(exc).__name__}: {exc}"}), flush=True)
        raise SystemExit(2)
