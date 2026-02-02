"""HLS playlist and segment management."""

import os
import re
import time
import logging
import threading
from pathlib import Path

from config import HLS_OUTPUT_DIR, HLS_SEGMENT_TIME, HLS_LIST_SIZE, MUX_MODE, ABR_VARIANTS

logger = logging.getLogger(__name__)


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


def cleanup_stale_segments_loop(stop_event: threading.Event):
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
