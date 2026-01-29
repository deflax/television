import threading
from typing import Callable, Optional


class VisitorTracker:
    """Thread-safe tracker for currently active visitors.

    Visitors are tracked by SSE connection state. Each connected
    SSE client counts as an active visitor. No polling needed.
    
    Supports optional callbacks for connect/disconnect events.
    """

    def __init__(
        self,
        on_connect: Optional[Callable[[str, int], None]] = None,
        on_disconnect: Optional[Callable[[str, int], None]] = None
    ):
        """Initialize the visitor tracker.
        
        Args:
            on_connect: Callback called when a new unique visitor connects.
                        Receives (ip, visitor_count).
            on_disconnect: Callback called when a visitor fully disconnects.
                           Receives (ip, visitor_count).
        """
        self._visitors: dict[str, int] = {}  # ip -> connection count
        self._lock = threading.Lock()
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect

    def connect(self, ip: str) -> None:
        """Register an SSE client connection."""
        is_new_visitor = False
        current_count = 0
        
        with self._lock:
            prev_count = self._visitors.get(ip, 0)
            self._visitors[ip] = prev_count + 1
            is_new_visitor = prev_count == 0
            current_count = len(self._visitors)
        
        # Call callback outside of lock to avoid potential deadlocks
        if is_new_visitor and self._on_connect:
            self._on_connect(ip, current_count)

    def disconnect(self, ip: str) -> None:
        """Unregister an SSE client connection."""
        is_fully_disconnected = False
        current_count = 0
        
        with self._lock:
            if ip in self._visitors:
                self._visitors[ip] -= 1
                if self._visitors[ip] <= 0:
                    del self._visitors[ip]
                    is_fully_disconnected = True
            current_count = len(self._visitors)
        
        # Call callback outside of lock to avoid potential deadlocks
        if is_fully_disconnected and self._on_disconnect:
            self._on_disconnect(ip, current_count)

    @property
    def count(self) -> int:
        """Return the number of currently active visitors."""
        with self._lock:
            return len(self._visitors)

    @property
    def visitors(self) -> dict[str, int]:
        """Return a snapshot of currently active visitors (ip -> connection count)."""
        with self._lock:
            return self._visitors.copy()
