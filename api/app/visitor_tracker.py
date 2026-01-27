import time
import threading


class VisitorTracker:
    """Thread-safe tracker for currently active visitors.

    Visitors are identified by IP address and considered active if they
    have sent a heartbeat within the configured timeout period.
    """

    def __init__(self, timeout: int = 30):
        """Initialize the visitor tracker.

        Args:
            timeout: Seconds after which a visitor is considered inactive.
        """
        self._visitors: dict[str, float] = {}  # ip -> last_seen timestamp
        self._lock = threading.Lock()
        self._timeout = timeout

    def heartbeat(self, ip: str) -> None:
        """Register a heartbeat from a visitor."""
        with self._lock:
            self._visitors[ip] = time.time()

    def _cleanup(self) -> None:
        """Remove expired visitors. Must be called with lock held."""
        now = time.time()
        expired = [
            ip for ip, last_seen in self._visitors.items()
            if now - last_seen > self._timeout
        ]
        for ip in expired:
            del self._visitors[ip]

    @property
    def count(self) -> int:
        """Return the number of currently active visitors."""
        with self._lock:
            self._cleanup()
            return len(self._visitors)
