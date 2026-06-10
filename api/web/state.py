# pyright: reportImplicitRelativeImport=false

import asyncio
import ipaddress
from dataclasses import dataclass, field

from web.timecode_manager import TimecodeManager
from web.visitor_tracker import VisitorTracker


@dataclass
class WebRouteState:
    """Shared mutable web-route state used by route registration modules."""

    timecode_manager: TimecodeManager
    visitor_tracker: VisitorTracker
    sse_clients: set[asyncio.Queue[dict[str, str]]] = field(default_factory=set)
    hls_viewer_count: int = 0
    hls_viewer_ips: set[str] = field(default_factory=set)
    hls_viewer_last_seen: dict[str, float] = field(default_factory=dict)
    recent_sse_disconnects: dict[str, float] = field(default_factory=dict)


HLS_VIEWER_DISPLAY_GRACE_SECONDS = 240.0


def hls_viewer_display_key(viewer_key: str) -> str:
    """Return the public display identity for a raw mux HLS viewer key."""
    try:
        addr = ipaddress.ip_address(viewer_key)
    except ValueError:
        return viewer_key

    if isinstance(addr, ipaddress.IPv6Address):
        return str(ipaddress.ip_network(f'{addr}/64', strict=False))

    return str(addr)


def apply_hls_viewer_display_grace(
    state: WebRouteState,
    reported_ips: set[str],
    now: float,
) -> set[str]:
    """Update and return grace-smoothed HLS viewer keys for display."""
    for ip in reported_ips:
        state.hls_viewer_last_seen[hls_viewer_display_key(ip)] = now

    hls_cutoff = now - HLS_VIEWER_DISPLAY_GRACE_SECONDS
    for ip, ts in list(state.hls_viewer_last_seen.items()):
        if ts < hls_cutoff:
            del state.hls_viewer_last_seen[ip]

    return set(state.hls_viewer_last_seen.keys())
