"""Playhead monitoring via Server-Sent Events from the API.

Connects to the API's ``/events`` SSE endpoint and watches for playhead
changes.  When the active stream URL changes, the configured callback is
invoked so the mux loop can trigger a transition.
"""

import json
import time
import logging
import threading
from typing import Callable, Optional

import httpx

from config import API_URL, rewrite_stream_url

logger = logging.getLogger(__name__)

# How often (seconds) to retry the API health check.
_HEALTH_POLL_INTERVAL = 5
# How often to log "still waiting" while the API is unreachable.
_HEALTH_LOG_EVERY = 6  # iterations (6 * 5 s = 30 s)
# Delay before reconnecting after an SSE error.
_SSE_RECONNECT_DELAY = 5


def wait_for_api(stop_event: threading.Event) -> bool:
    """Block until the API health endpoint returns 200."""
    logger.info(f'Waiting for API at {API_URL}...')
    attempt = 0
    while not stop_event.is_set():
        try:
            with httpx.Client() as client:
                resp = client.get(f'{API_URL}/health', timeout=5.0)
                if resp.status_code == 200:
                    logger.info('API is ready')
                    return True
        except Exception:
            pass
        attempt += 1
        if attempt % _HEALTH_LOG_EVERY == 1:
            logger.info('Waiting for API to be ready...')
        time.sleep(_HEALTH_POLL_INTERVAL)
    return False


class PlayheadMonitor:
    """Watch the API SSE stream and fire *on_url_change* on playhead updates."""

    def __init__(
        self,
        stop_event: threading.Event,
        on_url_change: Optional[Callable[[str, str], None]] = None,
    ):
        self.stop_event = stop_event
        self.on_url_change = on_url_change
        self._current_url: Optional[str] = None
        self._url_lock = threading.Lock()

    def get_current_url(self) -> Optional[str]:
        """Thread-safe read of the current stream URL."""
        with self._url_lock:
            return self._current_url

    def run(self) -> None:
        """Connect to the SSE endpoint and process events until stopped."""
        if not wait_for_api(self.stop_event):
            return

        logger.info(f'Connecting to API SSE at {API_URL}/events')

        while not self.stop_event.is_set():
            try:
                self._consume_sse()
            except httpx.HTTPStatusError as e:
                logger.error(f'HTTP error connecting to API: {e}')
                time.sleep(_SSE_RECONNECT_DELAY)
            except Exception as e:
                logger.error(f'Error connecting to API: {e}')
                time.sleep(_SSE_RECONNECT_DELAY)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _consume_sse(self) -> None:
        """Open a single SSE connection and process lines until it drops."""
        with httpx.stream('GET', f'{API_URL}/events', timeout=None) as response:
            response.raise_for_status()
            logger.info('SSE connection established')

            for line in response.iter_lines():
                if self.stop_event.is_set():
                    break
                self._handle_line(line)

    def _handle_line(self, line: str) -> None:
        if not line or line.startswith('event:'):
            return

        if not line.startswith('data: '):
            return

        try:
            data = json.loads(line[6:])  # strip 'data: ' prefix
        except json.JSONDecodeError:
            return

        new_url = data.get('head')
        if not new_url:
            return

        new_url = rewrite_stream_url(new_url)

        with self._url_lock:
            if self._current_url == new_url:
                return
            stream_name = data.get('name', 'unknown')
            logger.info(f'Playhead changed: {stream_name}')
            self._current_url = new_url

            if self.on_url_change:
                self.on_url_change(new_url, stream_name)
