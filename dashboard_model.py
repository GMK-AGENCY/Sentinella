"""Deterministic operational dashboard data; no I/O or UI actions."""
from datetime import datetime, timezone
import math
from schedule_display import schedule_display, clock_label


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


def automation_cards(config, health, now):
    """Configured features and observed activity stay distinct; no native I/O."""
    from automation_policy import app_enabled, command_approval_enabled
    from polling_policy import interval
    health = health or {}
    reader, supervisor = health.get('reader', {}), health.get('supervisor', {})
    recovery = health.get('recovery', {})
    poll, scans = interval(config,'notifications'), interval(config,'chats')
    components = {key:_component(key,health.get(key),now,max(90,poll*3) if key=='reader' else 90)
                  for key in ('reader','supervisor')}
    paused = bool(config.get('paused'))
    present = bool(supervisor.get('blocked_reason','').startswith('PC in uso')
                   and (_number(config.get('idle_seconds_before_actions')) or 0)>0)
    apps = config.get('apps', {})
    selected = [app for app in apps if app_enabled(config,app,'questions') or app_enabled(config,app,'approvals') or command_approval_enabled(config,app)]
    questions = [app for app in apps if app_enabled(config,app,'questions') and apps[app].get('process_path')]
    approvals = [app for app in apps if app_enabled(config,app,'approvals') and apps[app].get('process_path')]
    def clock(value):
        value = _stamp(value)
        try:return clock_label(value,now) if value is not None else 'non disponibile'
        except (OSError,OverflowError,ValueError):return 'non disponibile'
    def entry(key,title,badge,tone,detail,meta,enabled=True):
        return dict(id=key,title=title,badge=badge,tone=tone,detail=detail,meta=meta,enabled=enabled)
    notify = entry('notifications','Notifiche','In ascolto','good',
                   'Legge gli avvisi delle applicazioni.',f'Ogni {poll:g} s · lettura {clock(reader.get("last_successful_scan"))}')
    if reader.get('requested') is False or reader.get('state')=='stopped':
        notify.update(badge='Fermo',tone='off',detail='Il lettore delle notifiche è fermo.',meta='Riavvia dalle Impostazioni',enabled=False)
    elif reader.get('state')=='blocked':
        notify.update(badge='Da verificare',tone='warning',detail='La lettura delle notifiche non è disponibile.',meta='Controlla Avvio e diagnostica')
    elif components['reader']['status']!='fresh':
        notify.update(badge='In avvio' if components['reader']['status']=='unknown' else 'Da verificare',tone='warning',
                      detail=components['reader']['label'],meta='Ultima lettura: '+clock(reader.get('last_successful_scan')))
    checked = [_stamp(supervisor.get('last_chat_checks',{}).get(app)) for app in selected]
    checked = [value for value in checked if value is not None]
    waiting = set(supervisor.get('waiting_for_apps',[])) & set(selected)
    fresh_checks = len(checked)==len(selected) and all(-5<=now-value<=scans+60 for value in checked)
    chat = entry('chats','Controllo chat','Attivo','good','Rileva domande e richieste nelle chat.',
                 f'Ogni {scans/60:g} min · controllo {clock(min(checked) if checked else None)}',bool(selected))
    if not selected:
        chat.update(badge='Disattivo',tone='off',detail='Il controllo delle chat è disabilitato.',meta='Abilita domande o consensi in Impostazioni')
    elif paused:
        chat.update(badge='In pausa',tone='paused',detail='Controllo abilitato, sospeso dalla pausa generale.',meta='Riprende quando riattivi Sentinella')
    elif components['supervisor']['status']!='fresh':
        chat.update(badge='Da verificare',tone='warning',detail=components['supervisor']['label'],meta='Controlla Avvio e diagnostica')
    elif waiting:
        chat.update(badge='In attesa' if len(waiting)==len(selected) else 'Parziale',tone='warning',
                    detail='Attende l’apertura di '+', '.join(app.title() for app in sorted(waiting))+'.')
    elif not fresh_checks:
        chat.update(badge='Da verificare' if checked else 'In avvio',tone='warning',detail='Il prossimo controllo deve ancora essere verificato.')
    else:
        partial = [supervisor.get('last_scan_results',{}).get(app,{}) for app in selected if supervisor.get('last_scan_results',{}).get(app,{}).get('partial')]
        if partial:
            chat.update(badge='Parziale',tone='warning',detail=partial[0].get('partial_reason') or 'Una finestra non è stata verificata.')
        manual = [reason for app in selected for reason in supervisor.get('last_scan_results',{}).get(app,{}).get('manual_approvals',[]) if reason]
        if manual:
            chat.update(badge='Consenso manuale',tone='warning',detail=manual[0],meta='Gestisci la richiesta nella chat o verifica le opzioni in Impostazioni')
    chat['hint']=('Rilevamento domande: '+('attivo' if questions else 'disattivo')+
                  '\nConsensi ai file: '+('attivi' if approvals and config.get('enabled') else 'disattivi')+
                  '\nApprovazione comandi: '+('attiva' if any(command_approval_enabled(config,a) for a in apps) else 'disattivata')+
                  '\nApp: '+(', '.join(app.title() for app in selected) or 'nessuna configurata'))
    idle = (_number(config.get('idle_seconds_before_actions')) or 0)/60
    inactivity = f'{idle:g} min senza mouse o tastiera' if idle else 'Intervento senza attesa di inattività'
    replies = entry('replies','Risposte automatiche','Pronte','good','Risponde alle domande quando sei assente.','Opzione attiva · chat abilitate',bool(config.get('away_mode')))
    recover = entry('recovery','Recupero scritture','Pronto','good','Riprende le scritture interrotte.',f'Opzione attiva · {len(config.get("allowed_project_roots",[]))} cartelle autorizzate',bool(config.get('automatic_file_recovery')))
    for card in (replies,recover):
        if not card['enabled']:
            card.update(badge='Disattive' if card is replies else 'Disattivo',tone='off',
                        detail='Le domande restano da gestire manualmente.' if card is replies else 'Le scritture bloccate restano da gestire manualmente.',meta='Opzione disattivata in Impostazioni')
        elif paused:
            card.update(badge='In pausa',tone='paused',detail='Opzione attiva, sospesa dalla pausa generale.',meta='Riprende quando riattivi Sentinella')
        elif not (questions if card is replies else approvals and config.get('enabled') and config.get('allowed_project_roots')):
            card.update(badge='Da configurare',tone='warning',detail='Abilita il controllo delle domande.' if card is replies else 'Abilita i consensi e scegli le cartelle autorizzate.',meta='Completa la configurazione in Impostazioni')
        elif card is recover and recovery.get('action') in {'error','uncertain','scope_changed','identity_changed'}:
            card.update(badge='Da verificare',tone='warning',detail='Un recupero richiede verifica. Il tentativo resta protetto.',meta='Apri la diagnostica per i dettagli')
        elif components['supervisor']['status']!='fresh':
            card.update(badge='Da verificare',tone='warning',detail='Il controllore non ha aggiornamenti recenti.',meta='Opzione attiva · verifica il controllore')
        elif present:
            card.update(badge='In attesa',tone='waiting',detail='Opzione attiva. Attende l’inattività.',meta=inactivity)
        elif not fresh_checks or waiting:
            card.update(badge='In attesa',tone='waiting',detail='Opzione attiva. Attende le applicazioni.',meta='Riprende con il controllo delle chat')
        elif card is recover:
            updated = _stamp(recovery.get('updated_at'))
            if updated is None or not -5<=now-updated<=90:
                card.update(badge='Da verificare',tone='warning',detail='Il recupero non ha controlli recenti.',meta='Opzione attiva · verifica il recupero')
            elif recovery.get('action') in {'selected_chat','drafted','ready','awaiting_receipt'}:
                card.update(badge='In corso',tone='working',detail='Sta gestendo una scrittura interrotta.',meta='Attende la verifica del risultato')
    replies['hint']='Risposte alle domande delle chat. Gli invii programmati hanno opzioni proprie.\n'+replies['detail']
    return [notify,chat,replies,recover]


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
                         "frequency":frequency, "last_confirmed_at":last_by_job.get(job["id"]), "reason":reason,'hint':display.get('hint','')})
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
    return {"automations": automation_cards(config,health,now),
            "next": min(candidates, key=lambda item: (item["when"], item["job_id"]), default=None),
            "last_confirmed": last_confirmed, "attention": attention, "inactive_attention": inactive,
            "schedule": schedule, "counts": {"total": len(jobs), "enabled": sum(bool(job.get("enabled")) for job in jobs), "paused": sum(not bool(job.get("enabled")) for job in jobs)},
            "components": components, "paused": paused,
            "history_note": "Storico completo fornito." if envelope.get("complete") is True else "Solo gli eventi disponibili: non rappresentano il totale degli invii.",
            "metrics": {"confirmed_24h": count if exact else None, "exact": bool(exact), "source": aggregate.get("source") if exact else None}}
