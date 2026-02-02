"""Configuration and constants for the replay service."""

import os
import logging
import threading

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class QuietAccessFilter(logging.Filter):
    """Suppress noisy 200 OK access log lines for health checks, segments and playlists."""

    QUIET_PATHS = ('/health', '/segment_', '/playlist.m3u8')

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if '200' in msg and any(p in msg for p in self.QUIET_PATHS):
            return False
        return True


logging.getLogger('uvicorn.access').addFilter(QuietAccessFilter())

# Configuration from environment
RECORDINGS_DIR = os.environ.get('RECORDINGS_DIR', '/recordings')
LIBRARY_DIR = os.environ.get('LIBRARY_DIR', '/library')
HLS_BASE_DIR = '/tmp/hls'
HLS_SEGMENT_TIME = int(os.environ.get('HLS_SEGMENT_TIME', '4'))
HLS_LIST_SIZE = int(os.environ.get('HLS_LIST_SIZE', '20'))
VIDEO_BITRATE = os.environ.get('VIDEO_BITRATE', '4000k')
AUDIO_BITRATE = os.environ.get('AUDIO_BITRATE', '128k')
PORT = int(os.environ.get('REPLAY_PORT', '8090'))
SCAN_INTERVAL = int(os.environ.get('REPLAY_SCAN_INTERVAL', '60'))
SEGMENT_RETAIN_SECONDS = HLS_SEGMENT_TIME * HLS_LIST_SIZE * 3

# Reserved channel name (data/recorder is always mounted here)
RESERVED_CHANNEL = 'recorder'

# Global state
stop_event = threading.Event()
channels: dict[str, 'Channel'] = {}  # type: ignore[name-defined]
channels_lock = threading.Lock()
