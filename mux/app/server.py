"""HTTP server for HLS output.

Serves playlists dynamically from the segment store, ensuring
consistency during transitions. Segments are served directly from disk.
"""

import asyncio
import logging
from pathlib import Path

from quart import Quart, Response, abort, send_file

from config import HLS_OUTPUT_DIR, MUX_MODE, NUM_VARIANTS
from segment_store import segment_store
from utils import wait_for_stable_file

logger = logging.getLogger(__name__)

# Retry settings for segments that may still be writing
SEGMENT_RETRY_DELAYS = (0.1, 0.2, 0.3, 0.5, 0.7)


# Silence noisy access logs
class QuietAccessFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if '200' in msg and ('/health' in msg or '/live/' in msg):
            return False
        return True


logging.getLogger('uvicorn.access').addFilter(QuietAccessFilter())

app = Quart(__name__)


@app.route('/health')
async def health():
    """Health check endpoint."""
    # Check if we have any segments
    segments = await segment_store.get_segments(0, count=1)
    stream_ready = len(segments) > 0
    
    return {
        'status': 'ok',
        'stream_ready': stream_ready,
        'mode': MUX_MODE,
    }


@app.route('/live/stream.m3u8')
async def master_playlist():
    """Serve the master playlist."""
    if MUX_MODE == 'abr':
        content = await segment_store.generate_master_playlist()
    else:
        # In copy mode, serve the variant playlist directly
        content = await segment_store.generate_playlist(0)
    
    return Response(
        content,
        mimetype='application/vnd.apple.mpegurl',
        headers={
            'Cache-Control': 'no-cache, no-store, must-revalidate',
            'Access-Control-Allow-Origin': '*',
        }
    )


@app.route('/live/stream_<int:variant>/playlist.m3u8')
async def variant_playlist(variant: int):
    """Serve a variant playlist (ABR mode)."""
    if variant < 0 or variant >= NUM_VARIANTS:
        abort(404)
    
    content = await segment_store.generate_playlist(variant)
    
    return Response(
        content,
        mimetype='application/vnd.apple.mpegurl',
        headers={
            'Cache-Control': 'no-cache, no-store, must-revalidate',
            'Access-Control-Allow-Origin': '*',
        }
    )


@app.route('/live/stream_<int:variant>/<filename>')
async def variant_segment(variant: int, filename: str):
    """Serve a segment file from a variant directory."""
    if variant < 0 or variant >= NUM_VARIANTS:
        abort(404)
    
    if not filename.endswith('.ts'):
        abort(404)
    
    # Security check
    if '..' in filename or filename.startswith('/'):
        abort(403)
    
    file_path = Path(HLS_OUTPUT_DIR) / f'stream_{variant}' / filename
    return await _serve_segment(file_path)


@app.route('/live/<filename>')
async def segment(filename: str):
    """Serve a segment file (copy mode) or redirect."""
    # Security check
    if '..' in filename or filename.startswith('/'):
        abort(403)
    
    if filename.endswith('.m3u8'):
        # Serve playlist dynamically
        if filename == 'stream.m3u8':
            return await master_playlist()
        abort(404)
    
    if not filename.endswith('.ts'):
        abort(404)
    
    file_path = Path(HLS_OUTPUT_DIR) / filename
    return await _serve_segment(file_path)


async def _serve_segment(file_path: Path) -> Response:
    """Serve a segment file with retry logic for files being written."""
    # Retry if file doesn't exist yet
    if not file_path.exists():
        for delay in SEGMENT_RETRY_DELAYS:
            await asyncio.sleep(delay)
            if file_path.exists():
                break
    
    if not file_path.exists():
        logger.warning(f'Segment not found: {file_path.name}')
        abort(404)
    
    # Wait for file to be fully written
    await wait_for_stable_file(file_path)
    
    response = await send_file(
        file_path,
        mimetype='video/mp2t',
    )
    response.headers['Cache-Control'] = 'public, max-age=3600, immutable'
    response.headers['Access-Control-Allow-Origin'] = '*'
    return response


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8091)
