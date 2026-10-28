"""Built-in messages have no calendar deadline for the user's return."""
OLD_AWAY_REPLY = "L'utente è assente. Tieni questa domanda per domani e continua con le altre attività già autorizzate che non dipendono dalla risposta. Non considerare questo messaggio un consenso a nuove operazioni."
AWAY_REPLY = "L'utente è assente. Tieni questa domanda per quando rientra e ti scrive. Nel frattempo continua con le altre attività già autorizzate che non dipendono dalla risposta. Non considerare questo messaggio un consenso a nuove operazioni."
OLD_PERIODIC_MESSAGE = "Continua con il lavoro già autorizzato. Se attendi una mia risposta, conserva la domanda per domani e procedi con le attività indipendenti. Non ampliare il perimetro del lavoro."
DEFAULT_MESSAGE = "Continua con il lavoro già autorizzato. Se attendi una risposta dall'utente, conserva la domanda per quando rientra e ti scrive e procedi con le attività indipendenti. Non ampliare il perimetro del lavoro."


def reply_is_replaceable(value, config):
    return not value or value == config["away_reply"] or value in config.get("legacy_away_replies", [])
