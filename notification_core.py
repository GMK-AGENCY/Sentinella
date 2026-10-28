"""Classification only: notification text is data, never an instruction."""
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Notification:
    app: str
    app_id: str
    notification_id: int
    created: str
    title: str
    body: str
    source: str = "windows"

    @property
    def key(self):
        return (self.app_id, self.notification_id, self.created, self.title, self.body)


def is_target(app: str, app_id: str) -> bool:
    # The sender, not the message body, determines whether a notification is relevant.
    return bool(re.search(r"(?i)(?:^|[^a-z])(codex|claude)(?:$|[^a-z])", f"{app} {app_id}"))


def classify(title: str, body: str) -> str:
    text = f"{title} {body}".casefold()
    approvals = (
        r"consenti\s+(?:una|1)\s+volta", r"consenti\s+sempre", r"allow\s+(?:once|always)",
        r"(?:needs?|requires?|request(?:s|ed|ing)?)\s+(?:your\s+)?(?:approval|permission)",
        r"approval\s+(?:required|needed|request)", r"permission\s+(?:required|needed|request)",
        r"richiest[ao]\s+(?:di\s+)?(?:autorizzazione|approvazione|permesso)",
        r"(?:autorizzazione|approvazione|permesso)\s+(?:richiest[ao]|necessari[ao])",
        r"(?:vuoi|do you want to)\s+(?:consentire|autorizzare|allow|approve)",
    )
    if any(re.search(pattern, text) for pattern in approvals):
        return "Possibile approvazione"
    if any(s in text for s in ("needs your input", "needs attention", "requires your input", "waiting for you", "in attesa di risposta", "richiede attenzione", "ha una domanda", "your response", "bloccata", "bloccato", "is blocked")):
        return "Richiede attenzione"
    if any(s in text for s in ("failed", "error", "errore", "disconnected", "limit reached")):
        return "Errore / limite"
    if any(s in text for s in ("completed", "finished", "completato", "terminato")):
        return "Completata"
    return "Notifica"


class Deduplicator:
    def __init__(self, capacity=5000):
        self.capacity = capacity
        self.seen = {}

    def add(self, notification: Notification) -> bool:
        key = notification.key
        if key in self.seen:
            return False
        self.seen[key] = None
        while len(self.seen) > self.capacity:
            del self.seen[next(iter(self.seen))]
        return True
