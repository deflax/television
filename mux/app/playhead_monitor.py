"""Playhead monitoring via Server-Sent Events from the API.

Async implementation that connects to the API's /events SSE endpoint
and notifies when the playhead stream URL changes.
"""

import asyncio
import json
import logging
from typing import Optional, Callable, Awaitable

import httpx

from config import API_URL, rewrite_stream_url

logger = logging.getLogger(__name__)

# Retry settings
HEALTH_POLL_INTERVAL = 5.0
HEALTH_LOG_EVERY = 6  # Log every 30 seconds while waiting
SSE_RECONNECT_DELAY = 5.0
SSE_READ_TIMEOUT = 60.0  # Reconnect if no data received for this long


async def wait_for_api() -> bool:
    """Wait until the API health endpoint returns 200.
    
    Returns True when API is ready, False if cancelled.
    """
    logger.info(f'Waiting for API at {API_URL}...')
    attempt = 0
    
    async with httpx.AsyncClient() as client:
        while True:
            try:
                resp = await client.get(f'{API_URL}/health', timeout=5.0)
                if resp.status_code == 200:
                    logger.info('API is ready')
                    return True
                logger.debug(f'API returned status {resp.status_code}')
            except (httpx.RequestError, httpx.TimeoutException) as e:
                logger.debug(f'API not ready: {e}')
            except asyncio.CancelledError:
                return False
            
            attempt += 1
            if attempt % HEALTH_LOG_EVERY == 1:
                logger.info('Waiting for API to be ready...')
            
            await asyncio.sleep(HEALTH_POLL_INTERVAL)


class PlayheadMonitor:
    """Watch the API SSE stream for playhead changes.
    
    Usage:
        monitor = PlayheadMonitor(on_change=my_callback)
        await monitor.run()  # Runs until cancelled
    """
    
    def __init__(
        self,
        on_change: Optional[Callable[[str, str], Awaitable[None]]] = None,
    ):
        """
        Args:
            on_change: Async callback when URL changes. Args: (new_url, stream_name)
        """
        self._on_change = on_change
        self._current_url: Optional[str] = None
        self._lock = asyncio.Lock()
        self._running = False
    
    @property
    def current_url(self) -> Optional[str]:
        """Get the current stream URL."""
        return self._current_url
    
    async def run(self) -> None:
        """Connect to SSE and process events until cancelled."""
        if not await wait_for_api():
            return
        
        logger.info(f'Connecting to API SSE at {API_URL}/events')
        self._running = True
        
        while self._running:
            try:
                await self._consume_sse()
            except asyncio.CancelledError:
                logger.info('PlayheadMonitor cancelled')
                break
            except httpx.ReadTimeout:
                logger.warning(f'SSE read timeout after {SSE_READ_TIMEOUT}s, reconnecting...')
                await asyncio.sleep(1.0)  # Brief delay before reconnect
            except httpx.HTTPStatusError as e:
                logger.error(f'HTTP error from API: {e}')
                await asyncio.sleep(SSE_RECONNECT_DELAY)
            except Exception as e:
                logger.error(f'Error in SSE connection: {e}')
                await asyncio.sleep(SSE_RECONNECT_DELAY)
        
        self._running = False
    
    def stop(self) -> None:
        """Signal the monitor to stop."""
        self._running = False
    
    async def _consume_sse(self) -> None:
        """Open SSE connection and process events.
        
        Uses a read timeout to detect stale connections where the server
        stops sending data but doesn't close the connection.
        """
        # No connect timeout, but enforce read timeout for stale detection
        timeout = httpx.Timeout(connect=30.0, read=SSE_READ_TIMEOUT, write=None, pool=None)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream('GET', f'{API_URL}/events') as response:
                response.raise_for_status()
                logger.info('SSE connection established')
                
                async for line in response.aiter_lines():
                    if not self._running:
                        break
                    await self._handle_line(line)
    
    async def _handle_line(self, line: str) -> None:
        """Parse an SSE line and handle playhead changes.
        
        SSE format (per spec):
        - Lines starting with ':' are comments
        - 'event: <name>' sets event type
        - 'data: <content>' is the data payload
        - 'id: <id>' sets last event ID
        - 'retry: <ms>' sets reconnection time
        - Empty line dispatches the event
        """
        line = line.strip()
        
        # Ignore empty lines and comments
        if not line or line.startswith(':'):
            return
        
        # Handle event type (we currently ignore it but log for debugging)
        if line.startswith('event:'):
            event_type = line[6:].strip()
            logger.debug(f'SSE event type: {event_type}')
            return
        
        # Handle retry directive
        if line.startswith('retry:'):
            return
        
        # Handle id directive
        if line.startswith('id:'):
            return
        
        # Handle data - support both 'data: ' and 'data:' formats
        if line.startswith('data:'):
            data_str = line[5:].lstrip()
        else:
            return
        
        if not data_str:
            return
        
        try:
            data = json.loads(data_str)
        except json.JSONDecodeError as e:
            logger.debug(f'Failed to parse SSE data as JSON: {e}')
            return
        
        new_url = data.get('head')
        if not new_url:
            return
        
        # Rewrite URL if needed
        new_url = rewrite_stream_url(new_url)
        stream_name = data.get('name', 'unknown')
        
        async with self._lock:
            if self._current_url == new_url:
                return
            
            logger.info(f'Playhead changed: {stream_name} -> {new_url[:50]}...')
            self._current_url = new_url
        
        # Notify callback
        if self._on_change:
            try:
                await self._on_change(new_url, stream_name)
            except Exception as e:
                logger.error(f'Error in playhead change callback: {e}', exc_info=True)
