# pyright: reportImplicitRelativeImport=false

import asyncio
import ipaddress
from dataclasses import dataclass, field

from web.timecode_manager import TimecodeManager
from web.visitor_tracker import VisitorTracker


@dataclass(frozen=True, slots=True)
class HLSViewerSession:
    connected_seconds: float
    last_seen: float


@dataclass(frozen=True, slots=True)
class HLSViewerDisplayUpdate:
    displayed_ips: set[str]
    disconnected_durations: dict[str, float]


@dataclass
class WebRouteState:
    """Shared mutable web-route state used by route registration modules."""

    timecode_manager: TimecodeManager
    visitor_tracker: VisitorTracker
    sse_clients: set[asyncio.Queue[dict[str, str]]] = field(default_factory=set)
    hls_viewer_count: int = 0
    hls_viewer_ips: set[str] = field(default_factory=set)
    hls_viewer_last_seen: dict[str, float] = field(default_factory=dict)
    hls_viewer_connected_seconds: dict[str, float] = field(default_factory=dict)
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
    reported_sessions = {
        ip: HLSViewerSession(connected_seconds=0.0, last_seen=now)
        for ip in reported_ips
    }
    return update_hls_viewer_display_state(state, reported_sessions, now).displayed_ips


def update_hls_viewer_display_state(
    state: WebRouteState,
    reported_sessions: dict[str, HLSViewerSession],
    now: float,
) -> HLSViewerDisplayUpdate:
    for ip, session in reported_sessions.items():
        display_key = hls_viewer_display_key(ip)
        state.hls_viewer_last_seen[display_key] = session.last_seen
        state.hls_viewer_connected_seconds[display_key] = session.connected_seconds

    hls_cutoff = now - HLS_VIEWER_DISPLAY_GRACE_SECONDS
    disconnected_durations: dict[str, float] = {}
    for ip, ts in list(state.hls_viewer_last_seen.items()):
        if ts < hls_cutoff:
            connected_seconds = state.hls_viewer_connected_seconds.pop(ip, 0.0)
            disconnected_durations[ip] = connected_seconds
            del state.hls_viewer_last_seen[ip]

    return HLSViewerDisplayUpdate(
        displayed_ips=set(state.hls_viewer_last_seen.keys()),
        disconnected_durations=disconnected_durations,
    )
