"""Configuration and constants for the replay service."""

import os
import logging
import threading

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(name)s - %(levelname)s - %(message)s'
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

# S3/MinIO configuration
S3_ENABLED = os.environ.get('S3_ENABLED', 'false').lower() == 'true'
S3_ENDPOINT = os.environ.get('S3_ENDPOINT', '')  # e.g. http://minio:9000
S3_ACCESS_KEY = os.environ.get('S3_ACCESS_KEY', '')
S3_SECRET_KEY = os.environ.get('S3_SECRET_KEY', '')
S3_BUCKET = os.environ.get('S3_BUCKET', 'library')  # bucket root = library, subdirs = channels
S3_MOUNT_OPTIONS = os.environ.get('S3_MOUNT_OPTIONS', '')  # extra s3fs mount options

# Reserved channel name (data/recorder is always mounted here)
RESERVED_CHANNEL = 'recorder'

# Global state
stop_event = threading.Event()
channels: dict[str, 'Channel'] = {}  # type: ignore[name-defined]
channels_lock = threading.Lock()
