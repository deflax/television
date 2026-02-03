"""Mux Service - HLS stream multiplexer with ABR output.

Monitors the API playhead via SSE and switches between input streams to
produce a continuous ABR output stream at ``/live/stream.m3u8``.

Architecture
------------
- **PlayheadMonitor** – listens on the SSE endpoint and fires a callback
  when the source URL changes.
- **FFmpegManager** – starts / stops ffmpeg and tracks segment numbering.
- **StreamTransition** – orchestrates graceful overlap switches with
  discontinuity injection.
- **playlist_manager** – segment cleanup, directory setup, playlist helpers.
"""

import signal
import logging
import threading
import time

from config import (
    MUX_MODE, HLS_SEGMENT_TIME, HLS_LIST_SIZE,
    ABR_VARIANTS, ABR_PRESET, ABR_GOP_SIZE,
    RESTREAMER_PUBLIC_HOST, RESTREAMER_INTERNAL_URL,
)
from ffmpeg_manager import FFmpegManager
from playlist_manager import (
    setup_output_dir, get_next_segment_number,
    inject_discontinuity, cleanup_stale_segments_loop,
)
from playhead_monitor import PlayheadMonitor
from stream_transition import StreamTransition

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format='%(name)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)

logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# Shared threading primitives
# ---------------------------------------------------------------------------

stop_event = threading.Event()
restart_event = threading.Event()
segment_counter_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Crash recovery settings
# ---------------------------------------------------------------------------

_CRASH_RECOVERY_DELAY = 3  # seconds to wait before restarting after a crash


# ---------------------------------------------------------------------------
# Core loop
# ---------------------------------------------------------------------------

def run_ffmpeg_loop(
    ffmpeg_manager: FFmpegManager,
    playhead_monitor: PlayheadMonitor,
    transition: StreamTransition,
) -> None:
    """Continuously run ffmpeg, restarting on playhead changes or crashes.

    On a *playhead change* the :class:`StreamTransition` performs an
    overlapping switch so clients never lose content.  On a *crash* the
    service falls back to a simple stop-inject-restart cycle.
    """
    while not stop_event.is_set():
        url = playhead_monitor.get_current_url()
        if not url:
            logger.info('No stream URL set, waiting...')
            time.sleep(2)
            continue

        ffmpeg_manager.start(url)
        restart_event.clear()

        # Block until ffmpeg dies or a playhead change is signalled.
        _wait_for_event(ffmpeg_manager)

        if restart_event.is_set() and not stop_event.is_set():
            _handle_playhead_change(ffmpeg_manager, playhead_monitor, transition)
        elif not stop_event.is_set():
            _handle_crash(ffmpeg_manager)
        else:
            ffmpeg_manager.stop(get_next_segment_number)


def _wait_for_event(ffmpeg_manager: FFmpegManager) -> None:
    """Poll until ffmpeg exits or ``restart_event`` / ``stop_event`` fires."""
    while not stop_event.is_set() and not restart_event.is_set():
        if not ffmpeg_manager.is_running():
            exit_code = ffmpeg_manager.get_exit_code()
            logger.warning(f'ffmpeg exited with code {exit_code}')
            break
        time.sleep(1)


def _handle_playhead_change(
    ffmpeg_manager: FFmpegManager,
    playhead_monitor: PlayheadMonitor,
    transition: StreamTransition,
) -> None:
    """Delegate to :class:`StreamTransition` for a graceful switch."""
    new_url = playhead_monitor.get_current_url()
    if new_url:
        transition.execute(ffmpeg_manager, new_url)
    restart_event.clear()


def _handle_crash(ffmpeg_manager: FFmpegManager) -> None:
    """Stop, inject discontinuity, and pause before the loop retries."""
    logger.warning('ffmpeg crashed, attempting seamless recovery...')
    ffmpeg_manager.stop(get_next_segment_number)
    inject_discontinuity()
    time.sleep(_CRASH_RECOVERY_DELAY)


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------

def _shutdown(ffmpeg_manager: FFmpegManager) -> None:
    logger.info('Shutting down...')
    stop_event.set()
    restart_event.set()  # unblock the ffmpeg loop
    ffmpeg_manager.stop(get_next_segment_number)
    logger.info('Shutdown complete')


def _signal_handler(signum, _frame):
    logger.info(f'Received signal {signum}')
    stop_event.set()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    _log_startup_info()

    setup_output_dir()

    ffmpeg_manager = FFmpegManager(segment_counter_lock)
    transition = StreamTransition(segment_counter_lock)

    playhead_monitor = PlayheadMonitor(
        stop_event=stop_event,
        on_url_change=lambda _url, _name: restart_event.set(),
    )

    # Background threads
    threading.Thread(
        target=cleanup_stale_segments_loop,
        args=(stop_event,),
        daemon=True,
    ).start()

    threading.Thread(
        target=run_ffmpeg_loop,
        args=(ffmpeg_manager, playhead_monitor, transition),
        daemon=True,
    ).start()

    # Blocks until stop_event
    try:
        playhead_monitor.run()
    except KeyboardInterrupt:
        pass

    _shutdown(ffmpeg_manager)


def _log_startup_info() -> None:
    logger.info('Mux service starting...')
    logger.info(
        f'Mode: {MUX_MODE} | Segment time: {HLS_SEGMENT_TIME}s | '
        f'Playlist size: {HLS_LIST_SIZE}'
    )
    if MUX_MODE == 'abr':
        variant_desc = ', '.join(
            f"{v['height']}p@{v['video_bitrate']}" for v in ABR_VARIANTS
        )
        logger.info(
            f'ABR variants: source (copy) + {variant_desc} | '
            f'Preset: {ABR_PRESET} | GOP: {ABR_GOP_SIZE}'
        )
    if RESTREAMER_PUBLIC_HOST and RESTREAMER_INTERNAL_URL:
        logger.info(
            f'URL rewrite: {RESTREAMER_PUBLIC_HOST} -> {RESTREAMER_INTERNAL_URL}'
        )


if __name__ == '__main__':
    main()
