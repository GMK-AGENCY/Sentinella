"""Deterministic operational dashboard data; no I/O or UI actions."""
from datetime import datetime, timezone
import math
from schedule_display import schedule_display


def _number(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def _stamp(value):
    numeric = _number(value)
    if numeric is not None:
        return numeric
    if isinstance(value, str):
        try:
            date = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return date.replace(tzinfo=timezone.utc).timestamp() if date.tzinfo is None else date.timestamp()
        except ValueError:
            pass
    return None


def _component(key, data, now, limit):
    names = {"gui": "Interfaccia", "reader": "Lettura notifiche", "supervisor": "Controllo chat"}
    sources = {"gui": "logs/gui-heartbeat.json", "reader": "logs/health.json", "supervisor": "logs/supervisor-health.json"}
    data = data if isinstance(data, dict) else {}
    updated = _stamp(data.get("updated_at"))
    activity = _stamp(data.get("poll_updated_at")) if key == "gui" else _stamp(data.get("last_successful_scan")) if key == "reader" else updated
    observed = min(updated, activity) if updated is not None and activity is not None else None
    age = now - observed if observed is not None else None
    status, label = "unknown", "Attività non ancora verificata"
    if data.get("error") or data.get("state") in {"failed", "error", "stopped"}:
        status, label = "error", "Controllo non riuscito"
    elif age is not None:
        if age < -5:
            status, label = "unknown", "Orario del controllo da verificare"
        elif age > limit:
            status, label = "stale", "Nessun aggiornamento recente"
        else:
            status, label = "fresh", "Controllo aggiornato"
            if key == "supervisor" and data.get("waiting_for_apps"):
                label = "Attende l'apertura delle applicazioni"
    return {"name": names[key], "status": status, "label": label, "updated": observed,
            "age_seconds": max(0, age) if age is not None else None,
            "source": data.get("source", sources[key]), "limit_seconds": limit}


def snapshot(config, jobs, states, history, health, now):
    """Return next, last_confirmed, attention and source-backed component status.

    `states` is either rows or a job-id mapping. `health` contains gui/reader/
    supervisor records. `history` is events or {events, limit, complete,
    confirmed_24h:{count,since,until,complete,source}}. A limited event list is
    never used to claim an exact daily total. All timestamps remain epoch values
    for the caller to format in the user's timezone.
    """
    now = float(now)
    health = health or {}
    rows = states if isinstance(states, dict) else {row["id"]: row for row in states or []}
    envelope = history if isinstance(history, dict) else {"events": history or [], "complete": False}
    events = envelope.get("events", [])
    paused = bool(config.get("paused"))
    attention, inactive, candidates, schedule = [], [], [], []
    confirmed = [event for event in events if event.get("event") == "Consegnato con ricevuta" and _stamp(event.get("at")) is not None and _stamp(event.get("at")) <= now]
    last_by_job = {}
    for event in confirmed:
        key, at = event.get("job_id"), _stamp(event["at"])
        last_by_job[key] = max(at, last_by_job.get(key, at))
    for job in jobs:
        raw = rows.get(job["id"], {})
        current = raw if raw.get("version") == job.get("version") else {}
        enabled = bool(job.get("enabled"))
        phase = current.get("phase", "idle")
        due = _number(current.get("due"))
        interval = _number(job.get("interval_seconds"))
        activated = _number(job.get("activated_at"))
        if due is None and activated is not None and interval is not None:
            due = activated + interval
        frequency = (f"Ogni {interval / 60:g} min" if job.get("repeat", True) else f"Una volta, dopo {interval / 60:g} min") if interval is not None else "Frequenza da verificare"
        base = {"job_id": job["id"], "title": job.get("title", "Destinatario non disponibile"), "at": _stamp(current.get("reserved_at")) or _stamp(current.get("updated_at"))}
        display = schedule_display(job, current, config, now, health.get("supervisor", {}).get("blocked_reason"))
        key, reason, severity = display["status_key"], display["detail"], display["severity"]
        if due is None:
            key, reason, severity = "attention", "La prossima scadenza non è disponibile.", "warning"
        if key not in {"scheduled", "completed", "paused"}:
            entry = {**base, "reason": reason, "severity": severity}
            (attention if enabled and not paused else inactive).append(entry)
        elif phase == "uncertain" and reason:
            inactive.append({**base, "severity":"info", "reason":"Invio precedente: " + reason})
        if raw and not current and raw.get("phase") == "uncertain":
            inactive.append({**base, "severity":"info", "reason":"Una versione precedente ha una consegna non verificata; è separata dalla programmazione attuale."})
        schedule.append({"job_id":job["id"], "title":job.get("title", ""), "enabled":enabled,
                         "status":display["status"], "status_key":key, "when":due, "countdown":display["next_precise"],
                         "frequency":frequency, "last_confirmed_at":last_by_job.get(job["id"]), "reason":reason})
        if enabled and phase != "finished" and due is not None and key not in {"unconfirmed", "queued", "confirming"}:
            candidates.append({"job_id":job["id"], "title":job.get("title", ""), "when":due,
                               "countdown":"Invii in pausa" if paused else display["next_precise"], "frequency":frequency,
                               "reason":reason, "status":"paused" if paused else "scheduled" if key=="scheduled" else "waiting"})
    last = max(confirmed, key=lambda item: _stamp(item["at"]), default=None)
    last_confirmed = ({"job_id": last.get("job_id"), "title": last.get("title", ""), "at": _stamp(last["at"]),
                       "label": "Consegna verificata"} if last else None)
    aggregate = envelope.get("confirmed_24h", {})
    count = aggregate.get("count")
    exact = (aggregate.get("complete") is True and isinstance(count, int) and not isinstance(count, bool) and count >= 0
             and _stamp(aggregate.get("since")) == now - 86400 and _stamp(aggregate.get("until")) == now
             and isinstance(aggregate.get("source"), str) and aggregate["source"].strip().lower().startswith("sql"))
    poll = _number(config.get("notification_poll_seconds")) or 30
    components = [_component("gui", health.get("gui"), now, 90),
                  _component("reader", health.get("reader"), now, max(90, poll * 3)),
                  _component("supervisor", health.get("supervisor"), now, 90)]
    attention.sort(key=lambda item: (item["severity"] != "warning", item["at"] if item["at"] is not None else now, item["job_id"]))
    schedule.sort(key=lambda item: (not item["enabled"], item["when"] if item["when"] is not None else math.inf, item["job_id"]))
    return {"next": min(candidates, key=lambda item: (item["when"], item["job_id"]), default=None),
            "last_confirmed": last_confirmed, "attention": attention, "inactive_attention": inactive,
            "schedule": schedule, "counts": {"total": len(jobs), "enabled": sum(bool(job.get("enabled")) for job in jobs), "paused": sum(not bool(job.get("enabled")) for job in jobs)},
            "components": components, "paused": paused,
            "history_note": "Storico completo fornito." if envelope.get("complete") is True else "Solo gli eventi disponibili: non rappresentano il totale degli invii.",
            "metrics": {"confirmed_24h": count if exact else None, "exact": bool(exact), "source": aggregate.get("source") if exact else None}}
