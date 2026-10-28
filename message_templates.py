"""Default absence messages continue only work already authorized by the user."""
OLD_AWAY_REPLY = "L'utente è assente. Tieni questa domanda per domani e continua con le altre attività già autorizzate che non dipendono dalla risposta. Non considerare questo messaggio un consenso a nuove operazioni."
PREVIOUS_AWAY_REPLY = "L'utente è assente. Tieni questa domanda per quando rientra e ti scrive. Nel frattempo continua con le altre attività già autorizzate che non dipendono dalla risposta. Non considerare questo messaggio un consenso a nuove operazioni."
OLD_PERIODIC_MESSAGE = 'Continua con il lavoro già autorizzato. Se attendi una mia risposta, conserva la domanda per domani e procedi con le attività indipendenti. Non ampliare il perimetro del lavoro.'
PREVIOUS_DEFAULT_MESSAGE = "Continua con il lavoro già autorizzato. Se attendi una risposta dall'utente, conserva la domanda per quando rientra e ti scrive e procedi con le attività indipendenti. Non ampliare il perimetro del lavoro."
AUTONOMOUS_CONTINUATION = "L'utente è assente. Continua autonomamente fino a completare il lavoro già autorizzato, senza ampliare il perimetro. Se manca un’informazione o un permesso indispensabile, annota il punto per quando l’utente rientra e ti scrive, lascia sospesa soltanto l’attività che ne dipende e prosegui con le attività indipendenti già autorizzate. Rispetta i consensi richiesti: non aggirarli e non attribuirti nuove autorizzazioni. Conserva dati, bozze e modifiche già presenti e verifica i risultati del lavoro."
AWAY_REPLY = "Conserva questa domanda per quando rientra e ti scrive. " + AUTONOMOUS_CONTINUATION
DEFAULT_MESSAGE = AUTONOMOUS_CONTINUATION
LEGACY_AWAY_REPLIES = (OLD_AWAY_REPLY, PREVIOUS_AWAY_REPLY)
LEGACY_PERIODIC_MESSAGES = (OLD_PERIODIC_MESSAGE, PREVIOUS_DEFAULT_MESSAGE)


def reply_is_replaceable(value, config):
    return not value or value == config["away_reply"] or value in config.get("legacy_away_replies", [])
