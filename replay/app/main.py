"""
Replay Service - Serves multiple directories as HLS streams with endless shuffled repeat.

Each channel (directory) gets its own ffmpeg process and HLS output.
Channels are auto-discovered from the library directory.
"""

import asyncio
import threading
from pathlib import Path

from quart import Quart, send_file, abort, Response

from config import (
    logger,
    channels,
    channels_lock,
    PORT,
    SCAN_INTERVAL,
    RECORDINGS_DIR,
    LIBRARY_DIR,
    RESERVED_CHANNEL,
)
from channel import (
    Channel,
    start_channel,
    stop_all_channels,
    watch_library,
)


app = Quart(__name__)


@app.after_request
async def add_hls_headers(response: Response) -> Response:
    """Add CORS headers for HLS clients."""
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, HEAD, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = '*'
    return response


# --- HTTP Routes ---

@app.route('/health')
async def health():
    """Health check endpoint."""
    with channels_lock:
        channel_info = {
            name: {
                'files_count': len(ch.shuffled_files),
                'ready': Path(ch.hls_dir, 'playlist.m3u8').exists()
            }
            for name, ch in channels.items()
        }
    return {'status': 'ok', 'channels': channel_info}


@app.route('/<channel>/playlist.m3u8')
async def serve_playlist(channel: str):
    """Serve the HLS playlist for a channel."""
    with channels_lock:
        if channel not in channels:
            abort(404, f"Channel '{channel}' not found")
        hls_dir = channels[channel].hls_dir

    playlist_path = Path(hls_dir) / 'playlist.m3u8'
    if not playlist_path.exists():
        abort(503, "Stream not ready yet")

    response = await send_file(
        playlist_path,
        mimetype='application/vnd.apple.mpegurl',
        cache_timeout=0
    )
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


@app.route('/<channel>/<path:filename>')
async def serve_channel_file(channel: str, filename: str):
    """Serve HLS segments and other files for a channel."""
    if '..' in filename or filename.startswith('/'):
        abort(403)

    with channels_lock:
        if channel not in channels:
            abort(404, f"Channel '{channel}' not found")
        hls_dir = channels[channel].hls_dir

    file_path = Path(hls_dir) / filename

    if not file_path.exists() and filename.endswith('.ts'):
        await asyncio.sleep(0.5)

    if not file_path.exists():
        abort(404)

    if filename.endswith('.m3u8'):
        mimetype = 'application/vnd.apple.mpegurl'
        cache_timeout = 0
    elif filename.endswith('.ts'):
        mimetype = 'video/mp2t'
        cache_timeout = 3600
    else:
        mimetype = 'application/octet-stream'
        cache_timeout = 0

    response = await send_file(file_path, mimetype=mimetype, cache_timeout=cache_timeout)
    if filename.endswith('.m3u8'):
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    elif filename.endswith('.ts'):
        response.headers['Cache-Control'] = 'public, max-age=3600, immutable'
    return response


@app.route('/')
async def index():
    """Root endpoint with service info."""
    with channels_lock:
        channel_list = list(channels.keys())
    return {
        'service': 'replay',
        'description': 'Multi-channel HLS streaming service',
        'channels': channel_list,
        'endpoints': {
            '/<channel>/playlist.m3u8': 'HLS playlist for channel',
            '/health': 'Health check'
        }
    }


# --- Lifecycle ---

@app.before_serving
async def startup():
    """Initialize channels on startup."""
    # Start the recorder channel (always present)
    start_channel(RESERVED_CHANNEL, RECORDINGS_DIR)

    # Start library watcher in background
    watcher_thread = threading.Thread(target=watch_library, daemon=True)
    watcher_thread.start()
    logger.info(f"Library watcher started (scanning {LIBRARY_DIR} every {SCAN_INTERVAL}s)")

    # Give ffmpeg time to generate initial segments
    logger.info("Waiting for ffmpeg to generate initial segments...")
    await asyncio.sleep(3)


@app.after_serving
async def shutdown():
    """Cleanup on shutdown."""
    logger.info("Shutting down...")
    stop_all_channels()


def create_app():
    """Application factory."""
    return app


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=PORT)
