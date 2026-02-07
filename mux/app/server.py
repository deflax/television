"""HTTP server for HLS output.

Serves playlists dynamically from the segment store, ensuring
consistency during transitions. Segments are served directly from disk.
"""

import asyncio
import logging
from pathlib import Path

from quart import Quart, Response, abort, request, send_file

from config import HLS_OUTPUT_DIR, MUX_MODE, NUM_VARIANTS
from hls_viewer_tracker import hls_viewer_tracker
from segment_store import segment_store
from utils import wait_for_stable_file

logger = logging.getLogger(__name__)

# Retry settings for segments that may still be writing
SEGMENT_WAIT_INTERVAL = 0.2  # seconds between checks
SEGMENT_MAX_WAIT = 5.0  # maximum total wait time


# Silence noisy access logs (health checks and playlist requests only)
class QuietAccessFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        # Silence health checks
        if '200' in msg and '/health' in msg:
            return False
        # Silence playlist requests but not segment requests
        if '200' in msg and '.m3u8' in msg:
            return False
        return True


logging.getLogger('uvicorn.access').addFilter(QuietAccessFilter())

app = Quart(__name__)


# CORS headers for all responses
CORS_HEADERS = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
}

# Cache headers for playlists (must not be cached by CDN)
PLAYLIST_CACHE_HEADERS = {
    'Cache-Control': 'no-cache, no-store, must-revalidate, private, max-age=0',
    'CDN-Cache-Control': 'no-store',
    'Surrogate-Control': 'no-store',
    'Pragma': 'no-cache',
    'Expires': '0',
}

# Cache headers for segments (can be cached, but not too long)
SEGMENT_CACHE_HEADERS = {
    'Cache-Control': 'public, max-age=300, stale-while-revalidate=60',
}


@app.route('/live/stream_<int:variant>/<path:path>', methods=['OPTIONS'])
@app.route('/live/<path:path>', methods=['OPTIONS'])
async def cors_preflight(**kwargs):
    """Handle CORS preflight requests."""
    return Response('', status=204, headers=CORS_HEADERS)


@app.route('/health')
async def health():
    """Health check endpoint."""
    return {'status': 'ok', 'mode': MUX_MODE}


@app.route('/viewers')
async def viewers():
    """Return current HLS viewer count."""
    count = await hls_viewer_tracker.count
    return {'hls_viewers': count}


def _get_client_ip() -> str:
    """Get the client IP, respecting X-Forwarded-For from HAProxy."""
    forwarded_for = request.headers.get('X-Forwarded-For')
    if forwarded_for:
        return forwarded_for.split(',')[0].strip()
    return request.remote_addr or '0.0.0.0'


@app.route('/live/stream.m3u8')
async def master_playlist():
    """Serve the master playlist."""
    # Track this client as an HLS viewer
    await hls_viewer_tracker.record_playlist_fetch(_get_client_ip())

    if MUX_MODE == 'abr':
        content = await segment_store.generate_master_playlist()
    else:
        # In copy mode, serve the variant playlist directly
        content = await segment_store.generate_playlist(0)
    
    return Response(
        content,
        mimetype='application/vnd.apple.mpegurl',
        headers={**PLAYLIST_CACHE_HEADERS, **CORS_HEADERS},
    )


@app.route('/live/stream_<int:variant>/playlist.m3u8')
async def variant_playlist(variant: int):
    """Serve a variant playlist (ABR mode)."""
    if variant < 0 or variant >= NUM_VARIANTS:
        abort(404)
    
    # Track this client as an HLS viewer (variant fetches also count)
    await hls_viewer_tracker.record_playlist_fetch(_get_client_ip())

    content = await segment_store.generate_playlist(variant)
    
    return Response(
        content,
        mimetype='application/vnd.apple.mpegurl',
        headers={**PLAYLIST_CACHE_HEADERS, **CORS_HEADERS},
    )


def _is_safe_path(base_dir: Path, requested_path: Path) -> bool:
    """Check if requested_path is safely within base_dir (no traversal)."""
    try:
        # Resolve to absolute paths to handle symlinks and ..
        base_resolved = base_dir.resolve()
        requested_resolved = requested_path.resolve()
        # Check that the resolved path starts with the base directory
        return str(requested_resolved).startswith(str(base_resolved) + '/')
    except (OSError, ValueError):
        return False


@app.route('/live/stream_<int:variant>/<filename>')
async def variant_segment(variant: int, filename: str):
    """Serve a segment file from a variant directory."""
    if variant < 0 or variant >= NUM_VARIANTS:
        abort(404)
    
    if not filename.endswith('.ts'):
        abort(404)
    
    base_dir = Path(HLS_OUTPUT_DIR)
    file_path = base_dir / f'stream_{variant}' / filename
    
    # Security check - ensure path doesn't escape output directory
    if not _is_safe_path(base_dir, file_path):
        logger.warning(f'Path traversal attempt blocked: {filename}')
        abort(403)
    
    return await _serve_segment(file_path)


@app.route('/live/<filename>')
async def segment(filename: str):
    """Serve a segment file (copy mode) or redirect."""
    if filename.endswith('.m3u8'):
        # Serve playlist dynamically
        if filename == 'stream.m3u8':
            return await master_playlist()
        abort(404)
    
    if not filename.endswith('.ts'):
        abort(404)
    
    base_dir = Path(HLS_OUTPUT_DIR)
    file_path = base_dir / filename
    
    # Security check - ensure path doesn't escape output directory
    if not _is_safe_path(base_dir, file_path):
        logger.warning(f'Path traversal attempt blocked: {filename}')
        abort(403)
    
    return await _serve_segment(file_path)


async def _serve_segment(file_path: Path) -> Response:
    """Serve a segment file with retry logic for files being written."""
    total_wait = 0.0
    
    # Wait for file to appear (may still be written by FFmpeg)
    while not file_path.exists() and total_wait < SEGMENT_MAX_WAIT:
        await asyncio.sleep(SEGMENT_WAIT_INTERVAL)
        total_wait += SEGMENT_WAIT_INTERVAL
    
    if not file_path.exists():
        logger.warning(f'Segment not found after {total_wait:.1f}s: {file_path.name}')
        abort(404)
    
    # Wait for file to be fully written
    is_stable = await wait_for_stable_file(file_path, check_delay=0.2, max_attempts=25)
    if not is_stable:
        logger.warning(f'Segment not stable, serving anyway: {file_path.name}')
    
    response = await send_file(
        file_path,
        mimetype='video/mp2t',
    )
    # Apply cache and CORS headers
    for key, value in {**SEGMENT_CACHE_HEADERS, **CORS_HEADERS}.items():
        response.headers[key] = value
    return response


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8091)
