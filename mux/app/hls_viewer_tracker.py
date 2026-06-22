# pyright: reportImplicitRelativeImport=false

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
from dataclasses import dataclass
from typing import TypedDict

import httpx

from config import HLS_VIEWER_TTL

logger = logging.getLogger(__name__)

# How often (seconds) to clean up expired viewers
CLEANUP_INTERVAL = 10.0

# How often (seconds) to report viewer count to the API
REPORT_INTERVAL = 5.0


@dataclass(frozen=True, slots=True)
class ViewerSession:
    connected_at: float
    last_seen: float


class ViewerReport(TypedDict):
    connected_seconds: float


def _viewer_key(ip: str) -> str | None:
    """Return the counted viewer key, or None for private/internal IPs."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip

    if addr.is_private or addr.is_loopback:
        return None

    return str(addr)


class HLSViewerTracker:
    """Track active HLS viewers by playlist fetch activity.

    A viewer is considered active if they have fetched a playlist
    within the last VIEWER_TTL seconds. This matches how HLS clients
    work: they must poll the playlist every target duration.
    """

    def __init__(self):
        self._viewers: dict[str, ViewerSession] = {}
        self._lock: asyncio.Lock = asyncio.Lock()

    async def record_playlist_fetch(self, ip: str) -> None:
        """Record that an IP fetched a playlist."""
        viewer_key = _viewer_key(ip)
        if viewer_key is None:
            logger.debug(f'HLS playlist fetch ignored: ip={ip} reason=private_or_loopback')
            return

        now = time.monotonic()
        async with self._lock:
            previous_session = self._viewers.get(viewer_key)
            if previous_session is None:
                connected_at = now
                age = 0.0
                is_new_viewer = True
            else:
                connected_at = previous_session.connected_at
                age = now - previous_session.last_seen
                is_new_viewer = False
            self._viewers[viewer_key] = ViewerSession(connected_at=connected_at, last_seen=now)
            active_count = len(self._viewers)

        if is_new_viewer:
            logger.info(f'HLS viewer connected: viewer={viewer_key} active={active_count}')
        else:
            logger.debug(f'HLS viewer refreshed: viewer={viewer_key} ip={ip} age={age:.1f}s ttl={HLS_VIEWER_TTL:.1f}s')

    async def cleanup_expired(self) -> None:
        """Remove viewers that haven't fetched a playlist recently."""
        cutoff = time.monotonic() - HLS_VIEWER_TTL
        active_count = 0
        async with self._lock:
            expired = [ip for ip, session in self._viewers.items() if session.last_seen < cutoff]
            for ip in expired:
                del self._viewers[ip]
            if expired:
                logger.debug(f'Expired {len(expired)} HLS viewers; active={len(self._viewers)} ttl={HLS_VIEWER_TTL:.1f}s')
                active_count = len(self._viewers)

        for viewer_key in expired:
            logger.info(f'HLS viewer disconnected: viewer={viewer_key} active={active_count} reason=ttl_expired')

    @property
    async def count(self) -> int:
        """Return the number of currently active HLS viewers."""
        await self.cleanup_expired()
        async with self._lock:
            return len(self._viewers)

    @property
    async def viewers(self) -> dict[str, ViewerSession]:
        await self.cleanup_expired()
        async with self._lock:
            return self._viewers.copy()

    @property
    async def viewer_report(self) -> dict[str, ViewerReport]:
        await self.cleanup_expired()
        now = time.monotonic()
        async with self._lock:
            return {
                viewer_key: {
                    'connected_seconds': now - session.connected_at,
                }
                for viewer_key, session in self._viewers.items()
            }


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
                viewers = await hls_viewer_tracker.viewer_report

                try:
                    resp = await client.post(
                        f'{api_url}/hls-viewers',
                        json={
                            'count': count,
                            'viewers': viewers,
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
