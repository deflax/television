import asyncio
from dataclasses import dataclass, field

from web.timecode_manager import TimecodeManager
from web.visitor_tracker import VisitorTracker


@dataclass
class WebRouteState:
    """Shared mutable web-route state used by route registration modules."""

    timecode_manager: TimecodeManager
    visitor_tracker: VisitorTracker
    sse_clients: set[asyncio.Queue] = field(default_factory=set)
    hls_viewer_count: int = 0
    recent_sse_disconnects: dict[str, float] = field(default_factory=dict)
