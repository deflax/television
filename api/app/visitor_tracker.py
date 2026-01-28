import threading


class VisitorTracker:
    """Thread-safe tracker for currently active visitors.

    Visitors are tracked by SSE connection state. Each connected
    SSE client counts as an active visitor. No polling needed.
    """

    def __init__(self):
        """Initialize the visitor tracker."""
        self._visitors: dict[str, int] = {}  # ip -> connection count
        self._lock = threading.Lock()

    def connect(self, ip: str) -> None:
        """Register an SSE client connection."""
        with self._lock:
            self._visitors[ip] = self._visitors.get(ip, 0) + 1

    def disconnect(self, ip: str) -> None:
        """Unregister an SSE client connection."""
        with self._lock:
            if ip in self._visitors:
                self._visitors[ip] -= 1
                if self._visitors[ip] <= 0:
                    del self._visitors[ip]

    @property
    def count(self) -> int:
        """Return the number of currently active visitors."""
        with self._lock:
            return len(self._visitors)
