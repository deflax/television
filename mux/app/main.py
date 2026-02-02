"""
Mux Service - HLS stream multiplexer with ABR output.

Monitors the API's playhead via SSE and switches between input streams
to produce a continuous ABR output stream at /live/stream.m3u8.
"""

import sys
import time
import signal
import logging
import threading

from config import (
    MUX_MODE, HLS_SEGMENT_TIME, HLS_LIST_SIZE,
    ABR_VARIANTS, ABR_PRESET, ABR_GOP_SIZE,
    RESTREAMER_PUBLIC_HOST, RESTREAMER_INTERNAL_URL
)
from ffmpeg_manager import FFmpegManager
from playlist_manager import (
    setup_output_dir, get_next_segment_number,
    inject_discontinuity, cleanup_stale_segments_loop
)
from playhead_monitor import PlayheadMonitor

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Quiet down httpx logging
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)

# Global state
stop_event = threading.Event()
restart_event = threading.Event()
segment_counter_lock = threading.Lock()


def run_ffmpeg_loop(ffmpeg_manager: FFmpegManager, playhead_monitor: PlayheadMonitor):
    """Main loop that runs ffmpeg and restarts on playhead changes.
    
    Seamless switching: on playhead change, segment numbering continues
    and a #EXT-X-DISCONTINUITY marker is injected into the playlist.
    On crash, attempt seamless recovery with discontinuity marker.
    """
    while not stop_event.is_set():
        # Wait for a stream URL
        url = playhead_monitor.get_current_url()
        
        if not url:
            logger.info("No stream URL set, waiting...")
            time.sleep(2)
            continue
        
        # Start ffmpeg with current URL
        ffmpeg_manager.start(url)
        restart_event.clear()
        
        # Monitor ffmpeg and wait for restart signal or exit
        while not stop_event.is_set() and not restart_event.is_set():
            if not ffmpeg_manager.is_running():
                # ffmpeg exited
                exit_code = ffmpeg_manager.get_exit_code()
                logger.warning(f"ffmpeg exited with code {exit_code}")
                break
            time.sleep(1)
        
        # Stop ffmpeg (also updates segment_counter)
        ffmpeg_manager.stop(get_next_segment_number)
        
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


def cleanup(ffmpeg_manager: FFmpegManager):
    """Stop all processes and clean up."""
    logger.info("Shutting down...")
    stop_event.set()
    restart_event.set()  # Wake up ffmpeg loop
    ffmpeg_manager.stop(get_next_segment_number)
    logger.info("Shutdown complete")


def signal_handler(signum, frame):
    """Handle shutdown signals."""
    logger.info(f"Received signal {signum}")
    stop_event.set()


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
    
    # Initialize managers
    ffmpeg_manager = FFmpegManager(segment_counter_lock)
    
    # Callback when playhead URL changes
    def on_url_change(new_url: str, stream_name: str):
        restart_event.set()
    
    playhead_monitor = PlayheadMonitor(
        stop_event=stop_event,
        on_url_change=on_url_change
    )
    
    # Start stale segment cleanup thread
    cleanup_thread = threading.Thread(
        target=cleanup_stale_segments_loop,
        args=(stop_event,),
        daemon=True
    )
    cleanup_thread.start()
    
    # Start ffmpeg loop in background thread
    ffmpeg_thread = threading.Thread(
        target=run_ffmpeg_loop,
        args=(ffmpeg_manager, playhead_monitor),
        daemon=True
    )
    ffmpeg_thread.start()
    
    # Monitor playhead (blocks until stop_event)
    try:
        playhead_monitor.run()
    except KeyboardInterrupt:
        pass
    
    cleanup(ffmpeg_manager)


if __name__ == '__main__':
    main()
