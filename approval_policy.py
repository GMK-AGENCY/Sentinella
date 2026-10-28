"""Decisions for explicitly delegated local project file operations."""
from dataclasses import dataclass
from pathlib import Path
import re


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str


def is_inside(path, roots):
    candidate = Path(path)
    if not candidate.is_absolute():
        return False
    try:
        candidate = candidate.resolve()
        return any(candidate.is_relative_to(Path(root).resolve()) for root in roots)
    except (OSError, ValueError):
        return False


def file_change_decision(changes, roots, grant_root=None, prefer_always=True):
    """Structured Codex file-change approval, never arbitrary commands."""
    if not changes:
        return Decision("manual", "Mancano i file interessati dalla richiesta")
    for change in changes:
        if not isinstance(change, dict) or not is_inside(change.get("path", ""), roots):
            return Decision("manual", "Percorso non compreso nei progetti delegati")
        kind = change.get("kind")
        if isinstance(kind, dict):
            if kind.get("move_path") or kind.get("movePath"):
                return Decision("manual", "Spostamento non compreso nella delega di lettura/scrittura")
            kind = kind.get("type")
        if kind not in ("add", "update"):
            return Decision("manual", "Operazione diversa dalla creazione/modifica di file")
    if grant_root and not is_inside(grant_root, roots):
        return Decision("manual", "La concessione permanente amplierebbe la cartella delegata")
    return Decision("acceptForSession" if prefer_always else "accept", "Scrittura nei file di progetto autorizzata dall'utente")


ALWAYS = {"consenti sempre", "allow always", "always allow", "allow for this session", "consenti per questa sessione"}
ONCE = {"consenti", "consenti una volta", "consenti 1 volta", "allow", "allow once", "approve", "approva", "sì", "yes"}
DENY = {"nega", "rifiuta", "annulla", "deny", "decline", "cancel", "reject", "no"}


def normalize(text):
    return re.sub(r"\s+", " ", text or "").strip().casefold()


def pick_button(labels, prefer_always=True):
    eligible = [(label, button_key(label)) for label in labels]
    if prefer_always:
        for label, normalized in eligible:
            if normalized in ALWAYS:
                return label
    for label, normalized in eligible:
        if normalized in ONCE:
            return label
    return None


def button_key(label):
    value = normalize(label)
    # Claude exposes the session scope as part of the accessible button name.
    for suffix in (", si applica solo a questa sessione", ", applies only to this session"):
        if value.endswith(suffix):
            return value.removesuffix(suffix).strip()
    return value
