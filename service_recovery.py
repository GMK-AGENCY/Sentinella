"""Non-blocking recovery deadlines for Sentinella's two owned services."""


class ServiceRecovery:
    def __init__(self):
        self.failures = 0
        self.retry_at = None
        self.healthy_since = None

    def failed(self, now):
        self.failures += 1
        delay = min(60, 5 * 2 ** min(self.failures - 1, 4))
        self.retry_at = now + delay
        self.healthy_since = None
        return delay

    def due(self, now):
        return self.retry_at is not None and now >= self.retry_at

    def started(self):
        self.retry_at = None
        self.healthy_since = None

    def healthy(self, now):
        if self.healthy_since is None:
            self.healthy_since = now
        if now - self.healthy_since >= 120:
            self.failures = 0


def supervisor_stale(health, started_wall, now_wall):
    """Allow startup and a full bounded UI step; ignore a predecessor's file."""
    from datetime import datetime
    try:
        stamp = datetime.fromisoformat(health["updated_at"]).timestamp()
        if stamp < started_wall:
            stamp = started_wall
    except (KeyError, TypeError, ValueError):
        stamp = started_wall
    return now_wall - max(started_wall, stamp) > 90
