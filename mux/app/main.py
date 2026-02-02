"""
Mux Service - HLS stream multiplexer with ABR output.

Monitors the API's playhead via SSE and switches between input streams
to produce a continuous ABR output stream at /live/stream.m3u8.
"""

import os
import re
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

# Internal restreamer URL rewriting (bypass public hostname/Cloudflare)
RESTREAMER_INTERNAL_URL = os.environ.get('RESTREAMER_INTERNAL_URL', 'http://restreamer:8080')
RESTREAMER_PUBLIC_HOST = os.environ.get('CORE_API_HOSTNAME', '')

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


def rewrite_stream_url(url: str) -> str:
    """Rewrite public stream URL to use internal restreamer container.
    
    Replaces https://{RESTREAMER_PUBLIC_HOST}/... with {RESTREAMER_INTERNAL_URL}/...
    to bypass Cloudflare and route directly within Docker network.
    """
    if not RESTREAMER_PUBLIC_HOST or not RESTREAMER_INTERNAL_URL:
        return url
    
    public_prefix = f'https://{RESTREAMER_PUBLIC_HOST}/'
    if url.startswith(public_prefix):
        internal_url = RESTREAMER_INTERNAL_URL.rstrip('/') + '/' + url[len(public_prefix):]
        logger.debug(f'Rewrote URL: {url} -> {internal_url}')
        return internal_url
    
    return url


# Global state
current_stream_url: Optional[str] = None
stream_url_lock = threading.Lock()
stop_event = threading.Event()
restart_event = threading.Event()
ffmpeg_process: Optional[subprocess.Popen] = None

# Segment counter for seamless switching (persists across ffmpeg restarts)
segment_counter: int = 0
segment_counter_lock = threading.Lock()


def setup_output_dir():
    """Ensure output directory exists and create variant subdirs."""
    os.makedirs(HLS_OUTPUT_DIR, exist_ok=True)
    # Create subdirs for ABR mode (source + configured variants)
    num_streams = len(ABR_VARIANTS) + 1  # +1 for source copy
    for i in range(num_streams):
        os.makedirs(f'{HLS_OUTPUT_DIR}/stream_{i}', exist_ok=True)


def cleanup_output_dir():
    """Clean all files from output directory."""
    for f in Path(HLS_OUTPUT_DIR).rglob('*'):
        if f.is_file():
            try:
                f.unlink()
            except Exception as e:
                logger.warning(f"Could not remove {f}: {e}")


def get_next_segment_number() -> int:
    """Scan output directory for highest segment number and return next."""
    highest = -1
    for f in Path(HLS_OUTPUT_DIR).rglob('segment_*.ts'):
        match = re.search(r'segment_(\d+)\.ts$', f.name)
        if match:
            num = int(match.group(1))
            if num > highest:
                highest = num
    return highest + 1 if highest >= 0 else 0


def inject_discontinuity():
    """Inject #EXT-X-DISCONTINUITY tag into active playlist(s).
    
    Called between stopping old ffmpeg and starting new one on playhead change.
    Tells HLS players that the next segments may differ in codec/timing.
    """
    try:
        if MUX_MODE == 'abr':
            # ABR mode: inject into each variant playlist
            num_streams = len(ABR_VARIANTS) + 1
            for i in range(num_streams):
                playlist = Path(HLS_OUTPUT_DIR) / f'stream_{i}' / 'playlist.m3u8'
                if playlist.exists():
                    with open(playlist, 'a') as f:
                        f.write('#EXT-X-DISCONTINUITY\n')
            logger.debug(f"Injected discontinuity into {num_streams} variant playlists")
        else:
            # Copy mode: single playlist
            playlist = Path(HLS_OUTPUT_DIR) / 'stream.m3u8'
            if playlist.exists():
                with open(playlist, 'a') as f:
                    f.write('#EXT-X-DISCONTINUITY\n')
            logger.debug("Injected discontinuity into stream playlist")
    except Exception as e:
        logger.warning(f"Could not inject discontinuity marker: {e}")


def cleanup_stale_segments():
    """Background thread that removes old .ts segments to prevent disk filling up.
    
    Runs every 30 seconds, deletes segments older than 3x the playlist window.
    """
    max_age = HLS_LIST_SIZE * HLS_SEGMENT_TIME * 3  # seconds
    
    while not stop_event.is_set():
        stop_event.wait(30)
        if stop_event.is_set():
            break
        
        now = time.time()
        deleted = 0
        try:
            for f in Path(HLS_OUTPUT_DIR).rglob('segment_*.ts'):
                if f.is_file() and (now - f.stat().st_mtime) > max_age:
                    try:
                        f.unlink()
                        deleted += 1
                    except Exception:
                        pass
            if deleted:
                logger.debug(f"Cleaned up {deleted} stale segments")
        except Exception as e:
            logger.warning(f"Error during stale segment cleanup: {e}")


def build_copy_cmd(input_url: str, start_number: int = 0) -> list[str]:
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
        '-start_number', str(start_number),
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


def build_abr_cmd(input_url: str, start_number: int = 0) -> list[str]:
    """Build ffmpeg command for ABR mode (source passthrough + transcoded variants).
    
    Uses smart scaling that only transcodes resolutions below source:
    - Source is mapped directly from input (passthrough, no filter graph)
    - Variants are split and scaled via filter_complex
    - Variants are capped at source height (no upscaling)
    
    Variants are configurable via ABR_VARIANTS env var.
    """
    num_variants = len(ABR_VARIANTS)
    total_streams = num_variants + 1  # +1 for source passthrough
    
    # Build filter_complex for splitting and scaling (variants only, not source)
    split_outputs = ''.join(f'[v_{i}_in]' for i in range(num_variants))
    filter_parts = [f'[0:v]split={num_variants}{split_outputs}']
    
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
        # Stream 0: Source (direct passthrough, not from filter graph)
        '-map', '0:v',
        '-c:v:0', 'copy',
        '-map', '0:a',
        '-c:a:0', 'copy',
    ]
    
    # Add transcoded variants
    for i, variant in enumerate(ABR_VARIANTS):
        stream_idx = i + 1  # 0 is source passthrough
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
        '-start_number', str(start_number),
        '-hls_segment_filename', f'{HLS_OUTPUT_DIR}/stream_%v/segment_%05d.ts',
        '-master_pl_name', 'stream.m3u8',
        '-var_stream_map', var_stream_map,
        f'{HLS_OUTPUT_DIR}/stream_%v/playlist.m3u8'
    ])
    
    return cmd


def start_ffmpeg(input_url: str) -> subprocess.Popen:
    """Start ffmpeg based on configured MUX_MODE."""
    global ffmpeg_process

    with segment_counter_lock:
        start_num = segment_counter

    if MUX_MODE == 'abr':
        cmd = build_abr_cmd(input_url, start_number=start_num)
        variant_desc = ', '.join(f"{v['height']}p" for v in ABR_VARIANTS)
        mode_desc = f"ABR (source + {variant_desc})"
    else:
        cmd = build_copy_cmd(input_url, start_number=start_num)
        mode_desc = "copy (passthrough)"

    logger.info(f"Starting ffmpeg [{mode_desc}] segment={start_num} input={input_url}")

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
    """Stop the current ffmpeg process and update segment counter."""
    global ffmpeg_process, segment_counter
    if ffmpeg_process:
        logger.info("Stopping ffmpeg...")
        ffmpeg_process.terminate()
        try:
            ffmpeg_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            ffmpeg_process.kill()
        ffmpeg_process = None
        
        # Update segment counter from written files
        next_num = get_next_segment_number()
        with segment_counter_lock:
            segment_counter = next_num
        logger.debug(f"Segment counter updated to {next_num}")


def run_ffmpeg_loop():
    """Main loop that runs ffmpeg and restarts on playhead changes.
    
    Seamless switching: on playhead change, segment numbering continues
    and a #EXT-X-DISCONTINUITY marker is injected into the playlist.
    On crash, everything is reset (clean slate).
    """
    global ffmpeg_process, segment_counter

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

        # Stop ffmpeg (also updates segment_counter)
        stop_ffmpeg()

        if restart_event.is_set():
            # Playhead changed - seamless restart
            logger.info("Playhead changed, seamless restart...")
            inject_discontinuity()
            restart_event.clear()
        elif not stop_event.is_set():
            # ffmpeg crashed - treat like playhead change (seamless restart)
            logger.warning("ffmpeg crashed, attempting seamless recovery...")
            inject_discontinuity()
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
                                # Rewrite to internal URL if configured
                                new_url = rewrite_stream_url(new_url)
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
    logger.info(f"Mode: {MUX_MODE} | Segment time: {HLS_SEGMENT_TIME}s | Playlist size: {HLS_LIST_SIZE}")
    if MUX_MODE == 'abr':
        variant_desc = ', '.join(f"{v['height']}p@{v['video_bitrate']}" for v in ABR_VARIANTS)
        logger.info(f"ABR variants: source (copy) + {variant_desc} | Preset: {ABR_PRESET} | GOP: {ABR_GOP_SIZE}")
    if RESTREAMER_PUBLIC_HOST and RESTREAMER_INTERNAL_URL:
        logger.info(f"URL rewrite: {RESTREAMER_PUBLIC_HOST} -> {RESTREAMER_INTERNAL_URL}")

    # Setup
    setup_output_dir()

    # Start stale segment cleanup thread
    cleanup_thread = threading.Thread(target=cleanup_stale_segments, daemon=True)
    cleanup_thread.start()

    # Start ffmpeg loop in background thread
    ffmpeg_thread = threading.Thread(target=run_ffmpeg_loop, daemon=True)
    ffmpeg_thread.start()

    # Monitor playhead (blocks until stop_event)
    monitor_playhead()

    cleanup()


if __name__ == '__main__':
    main()
