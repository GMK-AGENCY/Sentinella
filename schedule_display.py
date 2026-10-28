"""A schedule's current version and the PC clock determine its display."""
from datetime import datetime, timezone
import math


def clock_label(timestamp, now):
    date = datetime.fromtimestamp(timestamp, timezone.utc).astimezone()
    today = datetime.fromtimestamp(now, timezone.utc).astimezone().date()
    if date.date() == today:
        return date.strftime("%H:%M")
    return date.strftime("%d/%m %H:%M")


def _schedule_display(job, stored, config, now, waiting_reason=None):
    # Configuration is authoritative; a paused/busy worker may not yet have
    # prepared this version. Never reuse the prior version's due or last_sent.
    state = stored if stored and stored.get("version") == job["version"] else {}
    due = state.get("due", job.get("activated_at", now) + job["interval_seconds"])
    phase = state.get("phase", "idle")
    last = state.get("last_sent")
    result = {"frequency": f"Ogni {job['interval_seconds'] / 60:g} min" if job.get("repeat", True) else f"Una volta, dopo {job['interval_seconds'] / 60:g} min", "last_sent": clock_label(last, now) if last else "Mai inviato", "detail": state.get("detail", "In attesa del primo invio"), "due": due}
    if not job.get("enabled") or config.get("paused"):
        return {**result, "status": "In pausa", "next": "Riprendi Sentinella" if config.get("paused") else "Riprendi il messaggio"}
    detail = result['detail'].casefold()
    before_send = phase != 'uncertain' or (state.get('local_stage') in {'reserved','drafted'} and 'bozza scomparsa' not in detail)
    pc_wait = ('cambio chat rinviato' in detail or
               due <= now and waiting_reason and waiting_reason.startswith('PC in uso') and not job.get('send_when_present'))
    if phase != 'finished' and before_send and pc_wait:
        return {**result, 'status':'In attesa', 'next':'PC in uso', 'hint':'Riprende automaticamente',
                'detail':'Il messaggio è pronto. Sentinella riprende automaticamente dopo l’inattività configurata, senza interrompere quello che stai facendo. Non devi premere alcun pulsante.'}
    if phase != 'finished' and before_send and 'applicazione in uso su un’altra chat' in detail:
        return {**result,'status':'In attesa','next':job.get('app','App').title()+' in uso',
                'hint':'Un’altra chat è in primo piano',
                'detail':'Stai usando un’altra chat nell’app destinataria. L’invio riprende in background senza spostare il focus o sovrascrivere il testo che stai scrivendo.'}
    if phase != 'finished' and before_send and 'impossibile verificare l’applicazione in primo piano' in detail:
        return {**result,'status':'Chat da verificare','next':'Verifica disponibilità dell’app'}
    if phase == "uncertain":
        if 'bozza scomparsa' in result['detail'].casefold():
            return {**result, 'status':'Da verificare', 'next':'Controlla il registro'}
        if 'messaggio accodato alla chat' in result['detail'].casefold():
            return {**result, 'status':'Accodato alla chat', 'next':'Attende l’elaborazione della chat'}
        if state.get("local_stage") in {"reserved", "drafted"}:
            if "bozza presente" in result["detail"].casefold():
                return {**result, "status": "Bozza presente", "next": "Campo messaggio già compilato"}
            return {**result, "status": "Preparazione", "next": "Invio in preparazione"}
        if state.get("local_stage") in {"sending", "awaiting_receipt"} and now - state.get("reserved_at", 0) < 120:
            return {**result, "status": "Verifica ricevuta", "next": "Consegna in verifica"}
        return {**result, "status": "Da verificare", "next": "Invio sospeso"}
    if phase == "finished":
        return {**result, "status": "Completato", "next": "Nessun altro invio"}
    detail = result["detail"].casefold()
    if "chat in elaborazione" in detail:
        return {**result, "status": "Attende la chat", "next": "Quando è libera"}
    if "bozza presente" in detail or "campo già compilato" in detail:
        return {**result, "status": "Bozza presente", "next": "Invio in attesa"}
    if "domanda o dialogo" in detail:
        return {**result, "status": "Richiesta aperta", "next": "Invio in attesa"}
    if "chat non trovata" in detail or "titolo ambiguo" in detail:
        return {**result, "status": "Chat da verificare", "next": "Invio in attesa"}
    if phase == 'drafted':
        return {**result, 'status':'Preparazione', 'next':'Messaggio pronto da inviare'}
    if due <= now:
        return {**result, "status": "In attesa", "next": "Appena possibile"}
    if due - now < 60:
        relative = "Tra meno di 1 min"
    else:
        relative = f"Tra {math.ceil((due - now) / 60)} min"
    return {**result, "status": "Programmato", "next": f"{relative} · {clock_label(due, now)}"}


def schedule_display(job, stored, config, now, waiting_reason=None):
    state = stored if stored and stored.get('version') == job['version'] else {}
    result = _schedule_display(job, stored, config, now, waiting_reason)
    paused = not job.get("enabled") or config.get("paused", False)
    activation = "In pausa" if paused else "Completato" if result["status"] == "Completato" else "Attivo"
    delta = int(math.ceil(result["due"] - now))
    seconds = abs(delta)
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    duration = (f"{hours} h " if hours else "") + f"{minutes:02d} min {seconds:02d} s"
    if paused:
        precise = 'Invii sospesi'
    elif activation == 'Completato':
        precise = 'Nessun altro invio'
    elif result['status'] == 'Verifica ricevuta':
        precise = 'Consegna in verifica'
    elif result['status'] == 'Da verificare':
        precise = 'Consegna da verificare'
    elif result['status'] == 'Bozza presente':
        precise = 'Bozza da completare'
    elif result['status'] in {'Attende la chat', 'Richiesta aperta', 'Chat da verificare', 'Preparazione'}:
        precise = result['next']
    elif delta <= 0:
        precise = 'Invio in attesa'
    else:
        precise = 'Tra ' + duration
    canonical = {'In pausa':('paused','In pausa'), 'Completato':('completed','Completato'),
                 'Bozza presente':('draft','Bozza nella chat'), 'Preparazione':('preparing','In preparazione'),
                 'Accodato alla chat':('queued','Accodato alla chat'),
                 'Verifica ricevuta':('confirming','Conferma in corso'), 'Da verificare':('unconfirmed','Ricezione non confermata'),
                 'Chat da verificare':('attention','Destinatario non disponibile'),
                 'Programmato':('scheduled','Programmato')}
    key, label = canonical.get(result['status'], ('waiting','In attesa'))
    if key != 'scheduled':
        precise = {'paused':'Nessun nuovo invio', 'completed':'—', 'draft':'Campo messaggio già compilato',
                   'preparing':result['next'], 'queued':'Attende l’elaborazione della chat',
                   'confirming':'Attende la ricevuta', 'unconfirmed':'Controlla il registro',
                   'attention':'Controlla il destinatario'}.get(key, result['next'])
    detail = result['detail']
    if key == 'unconfirmed':
        detail = 'Un tentativo precedente non ha ancora una ricevuta verificata. La programmazione è abilitata, ma quel tentativo non viene ripetuto per evitare duplicati.'
    elif key == 'draft':
        detail = 'Il campo messaggio nell’app destinataria contiene una bozza diversa da quella preparata da Sentinella. La programmazione è salvata; il testo nella chat resta intatto e blocca nuovi invii finché il campo non è libero.'
    elif key == 'paused':
        detail = ('La pausa generale sospende tutti i nuovi invii.' if config.get('paused') else 'Hai messo in pausa questa programmazione.')
        if state.get('phase') == 'uncertain':
            detail += ' Il tentativo precedente resta registrato nel registro e non verrà ripetuto senza prova.'
    return {**result, 'status':label, 'status_key':key,
            'severity':'warning' if key in {'draft','unconfirmed','attention'} else 'info',
            'detail':detail, "activation": activation, "activity": label, "next_precise": precise,
            "scheduled_at": clock_label(result["due"], now)}
