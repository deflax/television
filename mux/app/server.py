"""HTTP server to serve the muxed HLS stream."""

import asyncio
from pathlib import Path
from quart import Quart, send_file, abort

HLS_OUTPUT_DIR = '/tmp/hls'

app = Quart(__name__)


@app.route('/live/stream.m3u8')
async def serve_master_playlist():
    """Serve the HLS master playlist (ABR)."""
    playlist_path = Path(HLS_OUTPUT_DIR) / 'stream.m3u8'
    
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
    response.headers['Access-Control-Allow-Origin'] = '*'
    return response


@app.route('/live/<path:filename>')
async def serve_file(filename: str):
    """Serve HLS variant playlists and segments."""
    if '..' in filename or filename.startswith('/'):
        abort(403)
    
    file_path = Path(HLS_OUTPUT_DIR) / filename
    
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
    response.headers['Access-Control-Allow-Origin'] = '*'
    return response


@app.route('/health')
async def health():
    """Health check endpoint."""
    master_exists = Path(HLS_OUTPUT_DIR, 'stream.m3u8').exists()
    return {'status': 'ok', 'stream_ready': master_exists}


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8091)
