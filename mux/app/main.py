"""
Mux Service - HLS stream multiplexer with ABR output.

Monitors the API's playhead via SSE and switches between input streams
to produce a continuous ABR output stream at /live/stream.m3u8.
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

# Quiet down httpx logging
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)

# Configuration
API_URL = os.environ.get('API_URL', 'http://api:8080')
HLS_OUTPUT_DIR = '/tmp/hls'
HLS_SEGMENT_TIME = int(os.environ.get('HLS_SEGMENT_TIME', '4'))
HLS_LIST_SIZE = int(os.environ.get('HLS_LIST_SIZE', '20'))

# Mux mode: 'copy' (passthrough) or 'abr' (adaptive bitrate with source copy)
MUX_MODE = os.environ.get('MUX_MODE', 'copy').lower()

# Global state
current_stream_url: Optional[str] = None
stream_url_lock = threading.Lock()
stop_event = threading.Event()
restart_event = threading.Event()
ffmpeg_process: Optional[subprocess.Popen] = None


def setup_output_dir():
    """Ensure output directory exists and create variant subdirs."""
    os.makedirs(HLS_OUTPUT_DIR, exist_ok=True)
    # Create enough subdirs for ABR mode (up to 4 variants)
    for i in range(4):
        os.makedirs(f'{HLS_OUTPUT_DIR}/stream_{i}', exist_ok=True)


def cleanup_output_dir():
    """Clean old segments from output directory."""
    for f in Path(HLS_OUTPUT_DIR).rglob('*'):
        if f.is_file():
            try:
                f.unlink()
            except Exception as e:
                logger.warning(f"Could not remove {f}: {e}")


def build_copy_cmd(input_url: str) -> list[str]:
    """Build ffmpeg command for copy/passthrough mode (single stream, no transcoding)."""
    return [
        'ffmpeg',
        '-y',
        '-re',
        '-i', input_url,
        '-c:v', 'copy',
        '-c:a', 'copy',
        '-f', 'hls',
        '-hls_time', str(HLS_SEGMENT_TIME),
        '-hls_list_size', str(HLS_LIST_SIZE),
        '-hls_flags', 'append_list+omit_endlist',
        '-hls_segment_type', 'mpegts',
        '-hls_segment_filename', f'{HLS_OUTPUT_DIR}/segment_%05d.ts',
        f'{HLS_OUTPUT_DIR}/stream.m3u8'
    ]


def build_abr_cmd(input_url: str) -> list[str]:
    """Build ffmpeg command for ABR mode (source copy + transcoded 720p/576p).
    
    Uses smart scaling that only transcodes resolutions below source:
    - Source is always copied (no re-encoding)
    - 1080p: only transcode if source > 1080p
    - 720p: only transcode if source > 720p  
    - 576p: only transcode if source > 576p
    """
    return [
        'ffmpeg',
        '-y',
        '-re',
        '-i', input_url,
        # Split video for ABR variants
        '-filter_complex',
        '[0:v]split=4[v_src][v_1080_in][v_720_in][v_576_in]; '
        '[v_1080_in]scale=w=-2:h=\'min(1080,ih)\':force_original_aspect_ratio=decrease[v_1080]; '
        '[v_720_in]scale=w=-2:h=\'min(720,ih)\':force_original_aspect_ratio=decrease[v_720]; '
        '[v_576_in]scale=w=-2:h=\'min(576,ih)\':force_original_aspect_ratio=decrease[v_576]',
        # Stream 0: Source (copy)
        '-map', '[v_src]',
        '-c:v:0', 'copy',
        '-map', '0:a',
        '-c:a:0', 'copy',
        # Stream 1: 1080p
        '-map', '[v_1080]',
        '-c:v:1', 'libx264',
        '-preset', 'veryfast',
        '-b:v:1', '5000k',
        '-maxrate:v:1', '5350k',
        '-bufsize:v:1', '7500k',
        '-g:v:1', '48',
        '-sc_threshold:v:1', '0',
        '-map', '0:a',
        '-c:a:1', 'aac',
        '-b:a:1', '192k',
        '-ac:a:1', '2',
        # Stream 2: 720p
        '-map', '[v_720]',
        '-c:v:2', 'libx264',
        '-preset', 'veryfast',
        '-b:v:2', '2800k',
        '-maxrate:v:2', '2996k',
        '-bufsize:v:2', '4200k',
        '-g:v:2', '48',
        '-sc_threshold:v:2', '0',
        '-map', '0:a',
        '-c:a:2', 'aac',
        '-b:a:2', '128k',
        '-ac:a:2', '2',
        # Stream 3: 576p
        '-map', '[v_576]',
        '-c:v:3', 'libx264',
        '-preset', 'veryfast',
        '-b:v:3', '1400k',
        '-maxrate:v:3', '1498k',
        '-bufsize:v:3', '2100k',
        '-g:v:3', '48',
        '-sc_threshold:v:3', '0',
        '-map', '0:a',
        '-c:a:3', 'aac',
        '-b:a:3', '96k',
        '-ac:a:3', '2',
        # HLS output
        '-f', 'hls',
        '-hls_time', str(HLS_SEGMENT_TIME),
        '-hls_list_size', str(HLS_LIST_SIZE),
        '-hls_flags', 'independent_segments+append_list+omit_endlist',
        '-hls_segment_type', 'mpegts',
        '-hls_segment_filename', f'{HLS_OUTPUT_DIR}/stream_%v/segment_%05d.ts',
        '-master_pl_name', 'stream.m3u8',
        '-var_stream_map', 'v:0,a:0 v:1,a:1 v:2,a:2 v:3,a:3',
        f'{HLS_OUTPUT_DIR}/stream_%v/playlist.m3u8'
    ]


def start_ffmpeg(input_url: str) -> subprocess.Popen:
    """Start ffmpeg based on configured MUX_MODE."""
    global ffmpeg_process

    if MUX_MODE == 'abr':
        cmd = build_abr_cmd(input_url)
        mode_desc = "ABR (source copy + 1080p/720p/576p)"
    else:
        cmd = build_copy_cmd(input_url)
        mode_desc = "copy (passthrough)"

    logger.info(f"Starting ffmpeg [{mode_desc}] with input: {input_url}")

    ffmpeg_process = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE
    )

    # Monitor ffmpeg stderr in background
    def log_stderr():
        if ffmpeg_process and ffmpeg_process.stderr:
            for line in ffmpeg_process.stderr:
                decoded = line.decode().rstrip()
                if decoded:
                    logger.debug(f"ffmpeg: {decoded}")

    stderr_thread = threading.Thread(target=log_stderr, daemon=True)
    stderr_thread.start()

    return ffmpeg_process


def stop_ffmpeg():
    """Stop the current ffmpeg process."""
    global ffmpeg_process
    if ffmpeg_process:
        logger.info("Stopping ffmpeg...")
        ffmpeg_process.terminate()
        try:
            ffmpeg_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            ffmpeg_process.kill()
        ffmpeg_process = None


def run_ffmpeg_loop():
    """Main loop that runs ffmpeg and restarts on playhead changes."""
    global ffmpeg_process

    while not stop_event.is_set():
        # Wait for a stream URL
        with stream_url_lock:
            url = current_stream_url

        if not url:
            logger.info("No stream URL set, waiting...")
            time.sleep(2)
            continue

        # Start ffmpeg with current URL
        ffmpeg_process = start_ffmpeg(url)
        restart_event.clear()

        # Monitor ffmpeg and wait for restart signal or exit
        while not stop_event.is_set() and not restart_event.is_set():
            if ffmpeg_process.poll() is not None:
                # ffmpeg exited
                logger.warning(f"ffmpeg exited with code {ffmpeg_process.returncode}")
                break
            time.sleep(1)

        # Stop ffmpeg before restarting
        stop_ffmpeg()

        if restart_event.is_set():
            logger.info("Restarting ffmpeg with new stream...")
            restart_event.clear()
        elif not stop_event.is_set():
            # ffmpeg crashed, wait before retry
            logger.info("Restarting ffmpeg in 3 seconds...")
            time.sleep(3)


def wait_for_api():
    """Wait for API to be ready before connecting to SSE."""
    logger.info(f"Waiting for API at {API_URL}...")
    attempt = 0
    while not stop_event.is_set():
        try:
            with httpx.Client() as client:
                response = client.get(f'{API_URL}/health', timeout=5.0)
                if response.status_code == 200:
                    logger.info("API is ready")
                    return True
        except Exception:
            pass
        attempt += 1
        if attempt % 6 == 1:  # Log every 30 seconds (6 * 5s)
            logger.info("Waiting for API to be ready...")
        time.sleep(5)
    return False


def monitor_playhead():
    """Connect to API SSE endpoint and monitor playhead changes."""
    global current_stream_url

    # Wait for API to be ready first
    if not wait_for_api():
        return

    logger.info(f"Connecting to API SSE at {API_URL}/events")

    while not stop_event.is_set():
        try:
            with httpx.stream('GET', f'{API_URL}/events', timeout=None) as response:
                response.raise_for_status()
                logger.info("SSE connection established")

                for line in response.iter_lines():
                    if stop_event.is_set():
                        break

                    if not line:
                        continue

                    # Parse SSE format
                    if line.startswith('event:'):
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
                                        restart_event.set()
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
    logger.info("Shutting down...")
    stop_event.set()
    restart_event.set()  # Wake up ffmpeg loop
    stop_ffmpeg()
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

    # Start ffmpeg loop in background thread
    ffmpeg_thread = threading.Thread(target=run_ffmpeg_loop, daemon=True)
    ffmpeg_thread.start()

    # Monitor playhead (blocks until stop_event)
    monitor_playhead()

    cleanup()


if __name__ == '__main__':
    main()
