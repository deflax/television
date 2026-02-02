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

# ABR encoding settings
ABR_PRESET = os.environ.get('ABR_PRESET', 'veryfast')  # x264 preset
ABR_GOP_SIZE = int(os.environ.get('ABR_GOP_SIZE', '48'))  # Keyframe interval

# ABR variants configuration (JSON string or use defaults)
# Format: [{"height": 1080, "video_bitrate": "5000k", "audio_bitrate": "192k"}, ...]
DEFAULT_ABR_VARIANTS = [
    {"height": 1080, "video_bitrate": "5000k", "audio_bitrate": "192k"},
    {"height": 720, "video_bitrate": "2800k", "audio_bitrate": "128k"},
    {"height": 576, "video_bitrate": "1400k", "audio_bitrate": "96k"},
]


def parse_abr_variants() -> list[dict]:
    """Parse ABR_VARIANTS from environment or use defaults."""
    variants_json = os.environ.get('ABR_VARIANTS', '')
    if variants_json:
        try:
            variants = json.loads(variants_json)
            if isinstance(variants, list) and len(variants) > 0:
                logger.info(f"Using custom ABR variants: {variants}")
                return variants
        except json.JSONDecodeError as e:
            logger.warning(f"Invalid ABR_VARIANTS JSON, using defaults: {e}")
    return DEFAULT_ABR_VARIANTS


ABR_VARIANTS = parse_abr_variants()

# Global state
current_stream_url: Optional[str] = None
stream_url_lock = threading.Lock()
stop_event = threading.Event()
restart_event = threading.Event()
ffmpeg_process: Optional[subprocess.Popen] = None


def setup_output_dir():
    """Ensure output directory exists and create variant subdirs."""
    os.makedirs(HLS_OUTPUT_DIR, exist_ok=True)
    # Create subdirs for ABR mode (source + configured variants)
    num_streams = len(ABR_VARIANTS) + 1  # +1 for source copy
    for i in range(num_streams):
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


def parse_bitrate(bitrate_str: str) -> int:
    """Parse bitrate string (e.g., '5000k', '5M') to integer kbps."""
    bitrate_str = bitrate_str.lower().strip()
    if bitrate_str.endswith('m'):
        return int(float(bitrate_str[:-1]) * 1000)
    elif bitrate_str.endswith('k'):
        return int(float(bitrate_str[:-1]))
    return int(bitrate_str)


def build_abr_cmd(input_url: str) -> list[str]:
    """Build ffmpeg command for ABR mode (source copy + transcoded variants).
    
    Uses smart scaling that only transcodes resolutions below source:
    - Source is always copied (no re-encoding)
    - Variants are transcoded only if source height > variant height
    
    Variants are configurable via ABR_VARIANTS env var.
    """
    num_variants = len(ABR_VARIANTS)
    total_streams = num_variants + 1  # +1 for source copy
    
    # Build filter_complex for splitting and scaling
    split_outputs = '[v_src]' + ''.join(f'[v_{i}_in]' for i in range(num_variants))
    filter_parts = [f'[0:v]split={total_streams}{split_outputs}']
    
    for i, variant in enumerate(ABR_VARIANTS):
        height = variant['height']
        filter_parts.append(
            f"[v_{i}_in]scale=w=-2:h='min({height},ih)':force_original_aspect_ratio=decrease[v_{i}]"
        )
    
    filter_complex = '; '.join(filter_parts)
    
    cmd = [
        'ffmpeg',
        '-y',
        '-re',
        '-i', input_url,
        '-filter_complex', filter_complex,
        # Stream 0: Source (copy)
        '-map', '[v_src]',
        '-c:v:0', 'copy',
        '-map', '0:a',
        '-c:a:0', 'copy',
    ]
    
    # Add transcoded variants
    for i, variant in enumerate(ABR_VARIANTS):
        stream_idx = i + 1  # 0 is source copy
        video_bitrate = variant['video_bitrate']
        audio_bitrate = variant['audio_bitrate']
        
        # Calculate maxrate (7% buffer) and bufsize (1.5x bitrate)
        video_kbps = parse_bitrate(video_bitrate)
        maxrate = f"{int(video_kbps * 1.07)}k"
        bufsize = f"{int(video_kbps * 1.5)}k"
        
        cmd.extend([
            '-map', f'[v_{i}]',
            f'-c:v:{stream_idx}', 'libx264',
            f'-preset', ABR_PRESET,
            f'-b:v:{stream_idx}', video_bitrate,
            f'-maxrate:v:{stream_idx}', maxrate,
            f'-bufsize:v:{stream_idx}', bufsize,
            f'-g:v:{stream_idx}', str(ABR_GOP_SIZE),
            f'-sc_threshold:v:{stream_idx}', '0',
            '-map', '0:a',
            f'-c:a:{stream_idx}', 'aac',
            f'-b:a:{stream_idx}', audio_bitrate,
            f'-ac:a:{stream_idx}', '2',
        ])
    
    # Build var_stream_map (v:0,a:0 v:1,a:1 ...)
    var_stream_map = ' '.join(f'v:{i},a:{i}' for i in range(total_streams))
    
    cmd.extend([
        # HLS output
        '-f', 'hls',
        '-hls_time', str(HLS_SEGMENT_TIME),
        '-hls_list_size', str(HLS_LIST_SIZE),
        '-hls_flags', 'independent_segments+append_list+omit_endlist',
        '-hls_segment_type', 'mpegts',
        '-hls_segment_filename', f'{HLS_OUTPUT_DIR}/stream_%v/segment_%05d.ts',
        '-master_pl_name', 'stream.m3u8',
        '-var_stream_map', var_stream_map,
        f'{HLS_OUTPUT_DIR}/stream_%v/playlist.m3u8'
    ])
    
    return cmd


def start_ffmpeg(input_url: str) -> subprocess.Popen:
    """Start ffmpeg based on configured MUX_MODE."""
    global ffmpeg_process

    if MUX_MODE == 'abr':
        cmd = build_abr_cmd(input_url)
        variant_desc = ', '.join(f"{v['height']}p" for v in ABR_VARIANTS)
        mode_desc = f"ABR (source + {variant_desc})"
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
