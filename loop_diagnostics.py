"""Record a local stack only when the GUI stops servicing its heartbeat."""
import faulthandler
import traceback


class LoopDiagnostics:
    def __init__(self, path, timeout=35):
        self.timeout = timeout
        try:
            self.stream = path.open("a", encoding="utf-8", buffering=1)
        except OSError:
            self.stream = None

    def heartbeat(self):
        if self.stream is not None:
            # This replaces the previous deadline. Healthy loops write nothing.
            faulthandler.dump_traceback_later(self.timeout, file=self.stream)

    def callback_error(self, kind, value, tb):
        if self.stream is not None:
            traceback.print_exception(kind, value, tb, file=self.stream)
            self.stream.flush()

    def close(self):
        if self.stream is not None:
            faulthandler.cancel_dump_traceback_later()
            self.stream.close()
            self.stream = None
