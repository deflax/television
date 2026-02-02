"""Playhead monitoring via SSE from API."""

import time
import json
import logging
import threading
from typing import Optional, Callable

import httpx

from config import API_URL, rewrite_stream_url

logger = logging.getLogger(__name__)


def wait_for_api(stop_event: threading.Event) -> bool:
    """Wait for API to be ready before connecting to SSE."""
    logger.info(f"Waiting for API at {API_URL}...")
    attempt = 0
    while not stop_event.is_set():
        try:
            with httpx.Client() as client:
                response = client.get(f'{API_URL}/health', timeout=5.0)
                if response.status_code == 200:
                    logger.info("API is ready")
                    return True
        except Exception:
            pass
        attempt += 1
        if attempt % 6 == 1:  # Log every 30 seconds (6 * 5s)
            logger.info("Waiting for API to be ready...")
        time.sleep(5)
    return False


class PlayheadMonitor:
    """Monitors playhead changes via SSE and triggers callbacks."""
    
    def __init__(
        self,
        stop_event: threading.Event,
        on_url_change: Optional[Callable[[str, str], None]] = None
    ):
        """Initialize playhead monitor.
        
        Args:
            stop_event: Event to signal shutdown
            on_url_change: Callback when stream URL changes. Receives (new_url, stream_name)
        """
        self.stop_event = stop_event
        self.on_url_change = on_url_change
        self.current_url: Optional[str] = None
        self.url_lock = threading.Lock()
    
    def get_current_url(self) -> Optional[str]:
        """Get current stream URL thread-safely."""
        with self.url_lock:
            return self.current_url
    
    def run(self):
        """Connect to API SSE endpoint and monitor playhead changes."""
        # Wait for API to be ready first
        if not wait_for_api(self.stop_event):
            return
        
        logger.info(f"Connecting to API SSE at {API_URL}/events")
        
        while not self.stop_event.is_set():
            try:
                with httpx.stream('GET', f'{API_URL}/events', timeout=None) as response:
                    response.raise_for_status()
                    logger.info("SSE connection established")
                    
                    for line in response.iter_lines():
                        if self.stop_event.is_set():
                            break
                        
                        if not line:
                            continue
                        
                        # Parse SSE format
                        if line.startswith('event:'):
                            continue
                        
                        if line.startswith('data: '):
                            data_json = line[6:]  # Strip 'data: ' prefix
                            try:
                                data = json.loads(data_json)
                                new_url = data.get('head')
                                
                                if new_url:
                                    # Rewrite to internal URL if configured
                                    new_url = rewrite_stream_url(new_url)
                                    
                                    with self.url_lock:
                                        if self.current_url != new_url:
                                            stream_name = data.get('name', 'unknown')
                                            logger.info(f"Playhead changed: {stream_name}")
                                            self.current_url = new_url
                                            
                                            # Trigger callback
                                            if self.on_url_change:
                                                self.on_url_change(new_url, stream_name)
                            except json.JSONDecodeError:
                                pass
            
            except httpx.HTTPStatusError as e:
                logger.error(f"HTTP error connecting to API: {e}")
                time.sleep(5)
            except Exception as e:
                logger.error(f"Error connecting to API: {e}")
                time.sleep(5)
