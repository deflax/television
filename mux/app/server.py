"""HTTP server for the muxed HLS output stream.

Serves playlists and segments under ``/live/`` with transition-aware
retry logic so clients don't get spurious 404s during source switches.
"""

import asyncio
import logging
from pathlib import Path

from quart import Quart, send_file, abort

from config import HLS_OUTPUT_DIR

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Retry / stability constants
# ---------------------------------------------------------------------------

# Back-off schedule (seconds) when a .ts segment doesn't exist yet.
_SEGMENT_RETRY_DELAYS = (0.2, 0.3, 0.5, 0.7, 1.0)  # ~2.7 s total

# How long to pause before rechecking a missing playlist.
_PLAYLIST_RETRY_DELAY = 0.3  # seconds

# Brief pause used to detect whether a segment file is still being written.
_STABILITY_PROBE_DELAY = 0.05
_STABILITY_EXTRA_DELAY = 0.2


# ---------------------------------------------------------------------------
# Logging filter – silence noisy 200 OK lines for health / segment requests
# ---------------------------------------------------------------------------

class _QuietAccessFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if '200' in msg and '/health' in msg:
            return False
        if '200' in msg and ('/live/stream' in msg or '/live/segment_' in msg):
            return False
        return True


logging.getLogger('uvicorn.access').addFilter(_QuietAccessFilter())

app = Quart(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _wait_for_segment(file_path: Path, filename: str) -> None:
    """Retry with back-off until *file_path* appears on disk."""
    for attempt, delay in enumerate(_SEGMENT_RETRY_DELAYS):
        await asyncio.sleep(delay)
        if file_path.exists():
            logger.debug(
                f'Segment {filename} available after {attempt + 1} retries'
            )
            return


async def _ensure_segment_stable(file_path: Path) -> None:
    """Wait until the segment file size stops growing.

    FFmpeg may still be flushing the final bytes when a client requests a
    segment that just appeared on disk.
    """
    try:
        size_before = file_path.stat().st_size
        await asyncio.sleep(_STABILITY_PROBE_DELAY)
        size_after = file_path.stat().st_size
        if size_after > size_before:
            await asyncio.sleep(_STABILITY_EXTRA_DELAY)
    except Exception:
        pass  # best-effort; proceed even if stat fails


def _response_headers(filename: str) -> dict[str, str]:
    """Return Cache-Control / CORS headers appropriate for *filename*."""
    headers: dict[str, str] = {'Access-Control-Allow-Origin': '*'}
    if filename.endswith('.m3u8'):
        headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    elif filename.endswith('.ts'):
        headers['Cache-Control'] = 'public, max-age=3600, immutable'
    return headers


def _mimetype_for(filename: str) -> str:
    if filename.endswith('.m3u8'):
        return 'application/vnd.apple.mpegurl'
    if filename.endswith('.ts'):
        return 'video/mp2t'
    return 'application/octet-stream'


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route('/live/stream.m3u8')
async def serve_master_playlist():
    """Serve the HLS master playlist (ABR)."""
    playlist_path = Path(HLS_OUTPUT_DIR) / 'stream.m3u8'

    if not playlist_path.exists():
        abort(503, 'Stream not ready yet')

    response = await send_file(
        playlist_path,
        mimetype='application/vnd.apple.mpegurl',
        cache_timeout=0,
    )
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    response.headers['Access-Control-Allow-Origin'] = '*'
    return response


@app.route('/live/<path:filename>')
async def serve_file(filename: str):
    """Serve HLS variant playlists and segments.

    Includes transition-aware retry logic:
    - Segments (``.ts``): back-off retry + file-stability check.
    - Playlists (``.m3u8``): brief delay for discontinuity markers.
    """
    if '..' in filename or filename.startswith('/'):
        abort(403)

    file_path = Path(HLS_OUTPUT_DIR) / filename

    # --- Retry logic for files that may not exist yet ------------------
    if not file_path.exists():
        if filename.endswith('.ts'):
            await _wait_for_segment(file_path, filename)
        elif filename.endswith('.m3u8'):
            await asyncio.sleep(_PLAYLIST_RETRY_DELAY)

    if not file_path.exists():
        abort(404)

    # --- Stability check for segments ----------------------------------
    if filename.endswith('.ts'):
        await _ensure_segment_stable(file_path)

    # --- Serve the file ------------------------------------------------
    mimetype = _mimetype_for(filename)
    cache_timeout = 3600 if filename.endswith('.ts') else 0

    response = await send_file(file_path, mimetype=mimetype, cache_timeout=cache_timeout)
    for key, value in _response_headers(filename).items():
        response.headers[key] = value
    return response


@app.route('/health')
async def health():
    """Health check endpoint."""
    master_exists = Path(HLS_OUTPUT_DIR, 'stream.m3u8').exists()
    return {'status': 'ok', 'stream_ready': master_exists}


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8091)
