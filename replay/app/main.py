"""
Replay Service - Serves MP4 files as HLS streams with endless shuffled repeat.
"""

import asyncio
import os
import random
import subprocess
import signal
import sys
import threading
import time
import logging
from pathlib import Path
from typing import Optional
from quart import Quart, send_file, abort, Response, make_response

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Quart(__name__)


@app.after_request
async def add_hls_headers(response: Response) -> Response:
    """Add headers to prevent HTTP/2 stream issues with HLS clients like VLC."""
    # CORS - allow any player to access the stream
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, HEAD, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = '*'
    response.headers['Connection'] = 'keep-alive'
    return response


# Configuration
RECORDINGS_DIR = os.environ.get('RECORDINGS_DIR', '/recordings')
HLS_OUTPUT_DIR = '/tmp/hls'
HLS_SEGMENT_TIME = int(os.environ.get('HLS_SEGMENT_TIME', '4'))
HLS_LIST_SIZE = int(os.environ.get('HLS_LIST_SIZE', '20'))
VIDEO_BITRATE = os.environ.get('VIDEO_BITRATE', '4000k')
AUDIO_BITRATE = os.environ.get('AUDIO_BITRATE', '128k')
PORT = int(os.environ.get('REPLAY_PORT', '8090'))

# Keep segments on disk for 3x the playlist window so slow clients can still
# fetch them after they've rolled out of the playlist.  This replaces ffmpeg's
# delete_segments which removes files too aggressively for most HLS clients.
SEGMENT_RETAIN_SECONDS = HLS_SEGMENT_TIME * HLS_LIST_SIZE * 3

# Global state
ffmpeg_process: Optional[subprocess.Popen] = None
playlist_lock = threading.Lock()
current_file_index = 0
shuffled_files: list[str] = []
stop_event = threading.Event()


def get_mp4_files() -> list[str]:
    """Recursively find all MP4 files in the recordings directory."""
    recordings_path = Path(RECORDINGS_DIR)
    if not recordings_path.exists():
        logger.warning(f"Recordings directory does not exist: {RECORDINGS_DIR}")
        return []
    
    mp4_files = list(recordings_path.rglob('*.mp4'))
    mp4_files.extend(recordings_path.rglob('*.MP4'))
    
    # Convert to strings and sort for consistency before shuffling
    files = sorted([str(f) for f in mp4_files])
    logger.info(f"Found {len(files)} MP4 files in {RECORDINGS_DIR}")
    return files


def shuffle_playlist() -> list[str]:
    """Get all MP4 files and shuffle them."""
    global shuffled_files
    files = get_mp4_files()
    if files:
        random.shuffle(files)
        shuffled_files = files
        logger.info(f"Shuffled playlist with {len(files)} files")
    return shuffled_files


def create_concat_file(files: list[str]) -> str:
    """Create a concat demuxer file for ffmpeg."""
    concat_file = '/tmp/concat_list.txt'
    with open(concat_file, 'w') as f:
        for video_file in files:
            # Escape special characters for ffmpeg concat
            escaped = video_file.replace("'", "'\\''")
            f.write(f"file '{escaped}'\n")
    return concat_file


def cleanup_old_segments():
    """Periodically remove .ts segments older than SEGMENT_RETAIN_SECONDS.

    This replaces ffmpeg's delete_segments flag which removes files too
    aggressively — often before slow clients (like VLC) have fetched them.
    """
    hls_dir = Path(HLS_OUTPUT_DIR)
    while not stop_event.is_set():
        try:
            now = time.time()
            for seg in hls_dir.glob('segment_*.ts'):
                try:
                    age = now - seg.stat().st_mtime
                    if age > SEGMENT_RETAIN_SECONDS:
                        seg.unlink()
                except FileNotFoundError:
                    pass  # Already gone
                except Exception as e:
                    logger.warning(f"Could not remove old segment {seg}: {e}")
        except Exception as e:
            logger.warning(f"Segment cleanup error: {e}")
        # Run cleanup every segment duration
        stop_event.wait(timeout=HLS_SEGMENT_TIME)


def start_ffmpeg_stream():
    """Start ffmpeg process to generate HLS stream."""
    global ffmpeg_process, shuffled_files, current_file_index
    
    # Ensure output directory exists
    os.makedirs(HLS_OUTPUT_DIR, exist_ok=True)
    
    # Clean up old segments from previous runs
    for f in Path(HLS_OUTPUT_DIR).glob('*'):
        try:
            f.unlink()
        except Exception as e:
            logger.warning(f"Could not remove {f}: {e}")
    
    # Start the segment cleanup thread
    cleanup_thread = threading.Thread(target=cleanup_old_segments, daemon=True)
    cleanup_thread.start()
    logger.info(f"Segment cleanup thread started (retain for {SEGMENT_RETAIN_SECONDS}s)")
    
    while not stop_event.is_set():
        # Refresh and shuffle playlist
        shuffle_playlist()
        
        if not shuffled_files:
            logger.warning("No MP4 files found. Waiting 30 seconds before retry...")
            time.sleep(30)
            continue
        
        # Create concat file
        concat_file = create_concat_file(shuffled_files)
        
        # Build ffmpeg command for live HLS streaming
        # NOTE: We do NOT use delete_segments — our cleanup_old_segments thread
        # handles deletion with a much longer retention window so that slow
        # clients don't get 404s for segments still referenced in their copy
        # of the playlist.
        cmd = [
            'ffmpeg',
            '-re',  # Read input at native frame rate (important for live streaming)
            '-f', 'concat',
            '-safe', '0',
            '-stream_loop', '-1',  # Loop infinitely through the concat file
            '-i', concat_file,
            # Video encoding
            '-c:v', 'libx264',
            '-preset', 'veryfast',
            '-tune', 'zerolatency',
            '-b:v', VIDEO_BITRATE,
            '-maxrate', VIDEO_BITRATE,
            '-bufsize', str(int(VIDEO_BITRATE.replace('k', '000')) * 2),
            '-g', '48',  # Keyframe interval
            '-sc_threshold', '0',
            '-keyint_min', '48',
            # Audio encoding
            '-c:a', 'aac',
            '-b:a', AUDIO_BITRATE,
            '-ac', '2',
            '-ar', '44100',
            # HLS output
            '-f', 'hls',
            '-hls_time', str(HLS_SEGMENT_TIME),
            '-hls_list_size', str(HLS_LIST_SIZE),
            '-hls_flags', 'append_list+omit_endlist+temp_file',
            '-hls_segment_type', 'mpegts',
            '-hls_segment_filename', f'{HLS_OUTPUT_DIR}/segment_%05d.ts',
            f'{HLS_OUTPUT_DIR}/playlist.m3u8'
        ]
        
        logger.info(f"Starting ffmpeg with command: {' '.join(cmd)}")
        
        try:
            ffmpeg_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            
            # Monitor ffmpeg process
            while ffmpeg_process.poll() is None and not stop_event.is_set():
                time.sleep(1)
            
            if stop_event.is_set():
                break
                
            # Process ended unexpectedly
            stderr = ffmpeg_process.stderr.read().decode() if ffmpeg_process.stderr else ''
            logger.warning(f"ffmpeg process ended. Return code: {ffmpeg_process.returncode}")
            if stderr:
                logger.warning(f"ffmpeg stderr: {stderr[-1000:]}")  # Last 1000 chars
            
            # Wait before restarting
            logger.info("Restarting ffmpeg in 5 seconds...")
            time.sleep(5)
            
        except Exception as e:
            logger.error(f"Error running ffmpeg: {e}")
            time.sleep(5)


def stop_ffmpeg():
    """Stop the ffmpeg process gracefully."""
    global ffmpeg_process
    stop_event.set()
    
    if ffmpeg_process:
        logger.info("Stopping ffmpeg process...")
        ffmpeg_process.terminate()
        try:
            ffmpeg_process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            logger.warning("ffmpeg did not terminate, killing...")
            ffmpeg_process.kill()
        ffmpeg_process = None


@app.route('/health')
async def health():
    """Health check endpoint."""
    return {'status': 'ok', 'files_count': len(shuffled_files)}


@app.route('/playlist.m3u8')
async def serve_playlist():
    """Serve the HLS master playlist."""
    playlist_path = Path(HLS_OUTPUT_DIR) / 'playlist.m3u8'
    
    if not playlist_path.exists():
        logger.warning("Playlist not ready yet")
        abort(503, "Stream not ready yet")
    
    response = await send_file(
        playlist_path,
        mimetype='application/vnd.apple.mpegurl',
        cache_timeout=0
    )
    # Live HLS playlists must never be cached - stale playlists cause
    # clients to request segments that have already been deleted
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


@app.route('/segment_<int:segment_id>.ts')
async def serve_segment(segment_id: int):
    """Serve HLS segments with retry for race conditions."""
    segment_path = Path(HLS_OUTPUT_DIR) / f'segment_{segment_id:05d}.ts'
    
    # If segment doesn't exist yet, wait briefly — it may be mid-write
    # or the playlist was read just before the segment was flushed
    if not segment_path.exists():
        await asyncio.sleep(0.5)
    
    if not segment_path.exists():
        logger.warning(f"Segment not found: segment_{segment_id:05d}.ts")
        abort(404, "Segment not found")
    
    response = await send_file(
        segment_path,
        mimetype='video/mp2t',
        cache_timeout=3600  # Segments are immutable, can cache longer
    )
    response.headers['Cache-Control'] = 'public, max-age=3600, immutable'
    return response


@app.route('/<path:filename>')
async def serve_file(filename: str):
    """Generic file serving for any HLS files."""
    # Security: prevent path traversal
    if '..' in filename or filename.startswith('/'):
        abort(403)
    
    file_path = Path(HLS_OUTPUT_DIR) / filename
    
    # For .ts segments, briefly wait if not found — may be mid-write
    if not file_path.exists() and filename.endswith('.ts'):
        await asyncio.sleep(0.5)
    
    if not file_path.exists():
        if filename.endswith('.ts'):
            logger.warning(f"Segment not found via catch-all: {filename}")
        abort(404)
    
    # Determine mimetype and cache policy
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
    return {
        'service': 'replay',
        'description': 'HLS streaming service for recorded videos',
        'endpoints': {
            '/playlist.m3u8': 'HLS playlist',
            '/health': 'Health check'
        },
        'files_loaded': len(shuffled_files),
        'stream_ready': Path(HLS_OUTPUT_DIR, 'playlist.m3u8').exists()
    }


def start_ffmpeg_background():
    """Start ffmpeg in a background thread."""
    ffmpeg_thread = threading.Thread(target=start_ffmpeg_stream, daemon=True)
    ffmpeg_thread.start()
    logger.info("FFmpeg background thread started")


@app.before_serving
async def startup():
    """Initialize ffmpeg stream on startup."""
    start_ffmpeg_background()
    logger.info("Waiting for ffmpeg to generate initial segments...")
    await asyncio.sleep(3)


@app.after_serving
async def shutdown():
    """Cleanup on shutdown."""
    logger.info("Shutting down...")
    stop_ffmpeg()


def create_app():
    """Application factory for Hypercorn."""
    return app


if __name__ == '__main__':
    # Direct execution (development only)
    app.run(host='0.0.0.0', port=PORT)
