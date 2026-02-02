"""
Mux Service - Seamless HLS stream multiplexer.

Monitors the API's playhead via SSE and switches between input streams
to produce a single continuous output stream at /live/playlist.m3u8.
"""

import os
import sys
import time
import json
import signal
import logging
import subprocess
import threading
from pathlib import Path
from typing import Optional

import httpx

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
API_URL = os.environ.get('API_URL', 'http://api:8080')
HLS_OUTPUT_DIR = '/tmp/hls'
HLS_SEGMENT_TIME = int(os.environ.get('HLS_SEGMENT_TIME', '4'))
HLS_LIST_SIZE = int(os.environ.get('HLS_LIST_SIZE', '20'))
FIFO_PATH = '/tmp/input.ts'

# Global state
current_stream_url: Optional[str] = None
stream_url_lock = threading.Lock()
stop_event = threading.Event()
ffmpeg_process: Optional[subprocess.Popen] = None
fetcher_process: Optional[subprocess.Popen] = None


def setup_output_dir():
    """Ensure output directory exists and is clean."""
    os.makedirs(HLS_OUTPUT_DIR, exist_ok=True)
    for f in Path(HLS_OUTPUT_DIR).glob('*'):
        try:
            f.unlink()
        except Exception as e:
            logger.warning(f"Could not remove {f}: {e}")


def create_fifo():
    """Create named pipe for input switching."""
    try:
        if os.path.exists(FIFO_PATH):
            os.unlink(FIFO_PATH)
        os.mkfifo(FIFO_PATH)
        logger.info(f"Created FIFO at {FIFO_PATH}")
    except Exception as e:
        logger.error(f"Failed to create FIFO: {e}")
        sys.exit(1)


def fetch_and_write_to_fifo():
    """Continuously fetch the current stream and write to FIFO.
    
    This runs in a separate process to handle seamless input switching.
    """
    logger.info("Fetcher process started")
    
    while not stop_event.is_set():
        with stream_url_lock:
            url = current_stream_url
        
        if not url:
            logger.debug("No stream URL set, waiting...")
            time.sleep(1)
            continue
        
        logger.info(f"Fetching stream: {url}")
        
        try:
            # Fetch HLS stream and write to FIFO
            # ffmpeg reads from the FIFO continuously
            with httpx.stream('GET', url, timeout=30.0, follow_redirects=True) as response:
                response.raise_for_status()
                
                with open(FIFO_PATH, 'wb') as fifo:
                    for chunk in response.iter_bytes(chunk_size=8192):
                        if stop_event.is_set():
                            break
                        
                        # Check if URL changed (playhead switch)
                        with stream_url_lock:
                            if current_stream_url != url:
                                logger.info(f"Stream changed to: {current_stream_url}")
                                break
                        
                        fifo.write(chunk)
        
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error fetching stream: {e}")
            time.sleep(2)
        except Exception as e:
            logger.error(f"Error fetching stream: {e}")
            time.sleep(2)


def start_ffmpeg():
    """Start ffmpeg to read from FIFO and output ABR HLS with multiple quality levels."""
    global ffmpeg_process
    
    cmd = [
        'ffmpeg',
        '-re',
        '-i', FIFO_PATH,
        # Video filter: split into 3 streams - source quality (max 1080p), 720p, 576p
        '-filter_complex',
        '[0:v]split=3[v_src_in][v_720_in][v_576_in]; '
        '[v_src_in]scale=w=-2:h=\'min(1080,ih)\':force_original_aspect_ratio=decrease[v_src]; '
        '[v_720_in]scale=w=-2:h=\'min(720,ih)\':force_original_aspect_ratio=decrease[v_720]; '
        '[v_576_in]scale=w=-2:h=\'min(576,ih)\':force_original_aspect_ratio=decrease[v_576]',
        # Source quality (1080p)
        '-map', '[v_src]',
        '-c:v:0', 'libx264',
        '-preset', 'veryfast',
        '-b:v:0', '5000k',
        '-maxrate:v:0', '5350k',
        '-bufsize:v:0', '7500k',
        '-g:v:0', '48',
        '-sc_threshold:v:0', '0',
        '-map', '0:a',
        '-c:a:0', 'aac',
        '-b:a:0', '192k',
        '-ac:a:0', '2',
        # 720p variant
        '-map', '[v_720]',
        '-c:v:1', 'libx264',
        '-preset', 'veryfast',
        '-b:v:1', '2800k',
        '-maxrate:v:1', '2996k',
        '-bufsize:v:1', '4200k',
        '-g:v:1', '48',
        '-sc_threshold:v:1', '0',
        '-map', '0:a',
        '-c:a:1', 'aac',
        '-b:a:1', '128k',
        '-ac:a:1', '2',
        # 576p variant
        '-map', '[v_576]',
        '-c:v:2', 'libx264',
        '-preset', 'veryfast',
        '-b:v:2', '1400k',
        '-maxrate:v:2', '1498k',
        '-bufsize:v:2', '2100k',
        '-g:v:2', '48',
        '-sc_threshold:v:2', '0',
        '-map', '0:a',
        '-c:a:2', 'aac',
        '-b:a:2', '96k',
        '-ac:a:2', '2',
        # HLS output
        '-f', 'hls',
        '-hls_time', str(HLS_SEGMENT_TIME),
        '-hls_list_size', str(HLS_LIST_SIZE),
        '-hls_flags', 'independent_segments+append_list+omit_endlist+delete_segments',
        '-hls_segment_type', 'mpegts',
        '-hls_segment_filename', f'{HLS_OUTPUT_DIR}/stream_%v/segment_%05d.ts',
        '-master_pl_name', 'stream.m3u8',
        '-var_stream_map', 'v:0,a:0 v:1,a:1 v:2,a:2',
        f'{HLS_OUTPUT_DIR}/stream_%v/playlist.m3u8'
    ]
    
    logger.info(f"Starting ffmpeg: {' '.join(cmd)}")
    
    ffmpeg_process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    
    # Monitor ffmpeg stderr in background
    def log_stderr():
        if ffmpeg_process and ffmpeg_process.stderr:
            for line in ffmpeg_process.stderr:
                logger.debug(f"ffmpeg: {line.decode().rstrip()}")
    
    stderr_thread = threading.Thread(target=log_stderr, daemon=True)
    stderr_thread.start()
    
    return ffmpeg_process


def monitor_playhead():
    """Connect to API SSE endpoint and monitor playhead changes."""
    global current_stream_url
    
    logger.info(f"Connecting to API SSE at {API_URL}/events")
    
    while not stop_event.is_set():
        try:
            with httpx.stream('GET', f'{API_URL}/events', timeout=None) as response:
                response.raise_for_status()
                
                for line in response.iter_lines():
                    if stop_event.is_set():
                        break
                    
                    if not line:
                        continue
                    
                    # Parse SSE format
                    if line.startswith('event: playhead'):
                        continue
                    
                    if line.startswith('data: '):
                        data_json = line[6:]  # Strip 'data: ' prefix
                        try:
                            data = json.loads(data_json)
                            new_url = data.get('head')
                            
                            if new_url:
                                with stream_url_lock:
                                    if current_stream_url != new_url:
                                        logger.info(f"Playhead changed: {data.get('name', 'unknown')}")
                                        current_stream_url = new_url
                        except json.JSONDecodeError:
                            pass
        
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error connecting to API: {e}")
            time.sleep(5)
        except Exception as e:
            logger.error(f"Error connecting to API: {e}")
            time.sleep(5)


def cleanup():
    """Stop all processes and clean up."""
    global ffmpeg_process, fetcher_process
    
    logger.info("Shutting down...")
    stop_event.set()
    
    if fetcher_process:
        logger.info("Stopping fetcher process...")
        fetcher_process.terminate()
        try:
            fetcher_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            fetcher_process.kill()
    
    if ffmpeg_process:
        logger.info("Stopping ffmpeg...")
        ffmpeg_process.terminate()
        try:
            ffmpeg_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            ffmpeg_process.kill()
    
    if os.path.exists(FIFO_PATH):
        os.unlink(FIFO_PATH)
    
    logger.info("Shutdown complete")


def signal_handler(signum, frame):
    """Handle shutdown signals."""
    logger.info(f"Received signal {signum}")
    cleanup()
    sys.exit(0)


def main():
    """Main entry point."""
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    logger.info("Mux service starting...")
    
    # Setup
    setup_output_dir()
    create_fifo()
    
    # Start ffmpeg
    start_ffmpeg()
    
    # Start fetcher in background thread
    fetcher_thread = threading.Thread(target=fetch_and_write_to_fifo, daemon=True)
    fetcher_thread.start()
    
    # Monitor playhead (blocks until stop_event)
    monitor_playhead()
    
    cleanup()


if __name__ == '__main__':
    main()
