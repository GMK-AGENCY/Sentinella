"""Independent live polling intervals; deadlines never require long sleeps."""
import math

INTERVALS = {
    "notifications": ("notification_poll_seconds", 30, 5, 3600),
    "chats": ("chat_scan_seconds", 900, 60, 86400),
}


def validate_interval(kind, value):
    _, _, minimum, maximum = INTERVALS[kind]
    try:
        seconds = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        raise ValueError("Inserisci un numero valido per l'intervallo") from None
    if not math.isfinite(seconds) or not minimum <= seconds <= maximum:
        unit, low, high = ("secondi", minimum, maximum) if kind == "notifications" else ("minuti", minimum / 60, maximum / 60)
        raise ValueError(f"Intervallo {kind == 'notifications' and 'notifiche' or 'chat'}: da {low:g} a {high:g} {unit}")
    return seconds


def interval(config, kind):
    key, default, _, _ = INTERVALS[kind]
    try:
        return validate_interval(kind, config.get(key, default))
    except ValueError:
        return float(default)


class ScanClock:
    """Track completion separately for each app, using the current interval."""
    def __init__(self):
        self.last = {}
        self.retry_at = {}

    def due(self, key, now, seconds):
        if key in self.retry_at:
            return now >= self.retry_at[key]
        return key not in self.last or now >= self.last[key] + seconds

    def mark(self, key, now):
        self.last[key] = now
        self.retry_at.pop(key, None)

    def defer(self, key, now, seconds=30):
        self.retry_at[key] = now + seconds

    def remaining(self, key, now, seconds):
        if key in self.retry_at:
            return max(0, self.retry_at[key] - now)
        return max(0, self.last.get(key, now - seconds) + seconds - now)
