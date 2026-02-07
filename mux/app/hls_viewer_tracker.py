"""HLS viewer tracking based on playlist polling.

HLS clients must repeatedly fetch the m3u8 playlist to discover new segments.
We track unique IPs that have fetched a playlist within a recent time window
to count active HLS viewers.

Private/internal IPs (Docker, LAN, loopback) are excluded from the count.
"""

import asyncio
import ipaddress
import logging
import time
import httpx

logger = logging.getLogger(__name__)

# How long (seconds) since last playlist fetch before a viewer is considered gone
VIEWER_TTL = 30.0

# How often (seconds) to clean up expired viewers
CLEANUP_INTERVAL = 10.0

# How often (seconds) to report viewer count to the API
REPORT_INTERVAL = 5.0


def _is_private_ip(ip: str) -> bool:
    """Check if an IP address is private/internal (Docker, LAN, etc.)."""
    try:
        addr = ipaddress.ip_address(ip)
        return addr.is_private or addr.is_loopback
    except ValueError:
        return False


class HLSViewerTracker:
    """Track active HLS viewers by playlist fetch activity.

    A viewer is considered active if they have fetched a playlist
    within the last VIEWER_TTL seconds. This matches how HLS clients
    work: they must poll the playlist every target duration.
    """

    def __init__(self):
        self._viewers: dict[str, float] = {}  # ip -> last_seen timestamp
        self._lock = asyncio.Lock()

    async def record_playlist_fetch(self, ip: str) -> None:
        """Record that an IP fetched a playlist."""
        if _is_private_ip(ip):
            return

        async with self._lock:
            self._viewers[ip] = time.monotonic()

    async def cleanup_expired(self) -> None:
        """Remove viewers that haven't fetched a playlist recently."""
        cutoff = time.monotonic() - VIEWER_TTL
        async with self._lock:
            expired = [ip for ip, ts in self._viewers.items() if ts < cutoff]
            for ip in expired:
                del self._viewers[ip]
            if expired:
                logger.debug(f'Expired {len(expired)} HLS viewers')

    @property
    async def count(self) -> int:
        """Return the number of currently active HLS viewers."""
        async with self._lock:
            return len(self._viewers)

    @property
    async def viewers(self) -> dict[str, float]:
        """Return a snapshot of active viewers (ip -> last_seen)."""
        async with self._lock:
            return self._viewers.copy()


# Module-level singleton
hls_viewer_tracker = HLSViewerTracker()


async def cleanup_loop() -> None:
    """Background task to periodically clean up expired viewers."""
    while True:
        try:
            await asyncio.sleep(CLEANUP_INTERVAL)
            await hls_viewer_tracker.cleanup_expired()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f'HLS viewer cleanup error: {e}')


async def report_loop(api_url: str) -> None:
    """Background task to periodically report HLS viewer count to the API.

    POSTs the current HLS-only viewer count to the API so it can be
    combined with SSE viewer counts for the total.
    """
    async with httpx.AsyncClient() as client:
        while True:
            try:
                await asyncio.sleep(REPORT_INTERVAL)
                count = await hls_viewer_tracker.count
                viewers = await hls_viewer_tracker.viewers

                try:
                    resp = await client.post(
                        f'{api_url}/hls-viewers',
                        json={
                            'count': count,
                            'viewers': {ip: 1 for ip in viewers},
                        },
                        timeout=5.0,
                    )
                    if resp.status_code != 200:
                        logger.warning(f'API /hls-viewers returned {resp.status_code}')
                except (httpx.RequestError, httpx.TimeoutException) as e:
                    logger.debug(f'Failed to report HLS viewers to API: {e}')

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f'HLS viewer report error: {e}')
