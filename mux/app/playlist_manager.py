"""HLS playlist and segment management.

Responsible for:
- Creating / cleaning the HLS output directory tree.
- Tracking segment numbers across ffmpeg restarts.
- Injecting ``#EXT-X-DISCONTINUITY`` markers into playlists.
- Removing stale ``.ts`` segments in the background.
"""

import os
import re
import time
import logging
import threading
from pathlib import Path

from config import HLS_OUTPUT_DIR, HLS_SEGMENT_TIME, HLS_LIST_SIZE, MUX_MODE, ABR_VARIANTS

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Directory helpers
# ---------------------------------------------------------------------------

def setup_output_dir() -> None:
    """Ensure the output directory (and ABR variant sub-dirs) exist."""
    os.makedirs(HLS_OUTPUT_DIR, exist_ok=True)
    num_streams = len(ABR_VARIANTS) + 1  # +1 for source copy
    for i in range(num_streams):
        os.makedirs(f'{HLS_OUTPUT_DIR}/stream_{i}', exist_ok=True)


def cleanup_output_dir() -> None:
    """Remove every file under the output directory."""
    for f in Path(HLS_OUTPUT_DIR).rglob('*'):
        if f.is_file():
            try:
                f.unlink()
            except Exception as e:
                logger.warning(f'Could not remove {f}: {e}')


# ---------------------------------------------------------------------------
# Segment numbering
# ---------------------------------------------------------------------------

def get_next_segment_number() -> int:
    """Scan the output tree for the highest segment index and return index+1."""
    highest = -1
    for f in Path(HLS_OUTPUT_DIR).rglob('segment_*.ts'):
        match = re.search(r'segment_(\d+)\.ts$', f.name)
        if match:
            num = int(match.group(1))
            if num > highest:
                highest = num
    return highest + 1 if highest >= 0 else 0


# ---------------------------------------------------------------------------
# Discontinuity injection
# ---------------------------------------------------------------------------

def inject_discontinuity() -> None:
    """Inject ``#EXT-X-DISCONTINUITY`` into every active playlist.

    Uses :func:`_inject_discontinuity_into` which parses the playlist to
    find the correct insertion point, avoids duplicates, and writes
    atomically via a temp file.
    """
    try:
        playlists = _get_active_playlists()
        for playlist in playlists:
            if playlist.exists():
                _inject_discontinuity_into(playlist)
        logger.debug(f'Injected discontinuity into {len(playlists)} playlist(s)')
    except Exception as e:
        logger.warning(f'Could not inject discontinuity marker: {e}')


def _get_active_playlists() -> list[Path]:
    """Return the list of playlist paths that should receive a marker."""
    if MUX_MODE == 'abr':
        num_streams = len(ABR_VARIANTS) + 1
        return [
            Path(HLS_OUTPUT_DIR) / f'stream_{i}' / 'playlist.m3u8'
            for i in range(num_streams)
        ]
    return [Path(HLS_OUTPUT_DIR) / 'stream.m3u8']


def _inject_discontinuity_into(playlist_path: Path) -> None:
    """Parse *playlist_path* and insert a discontinuity after the last segment.

    * Avoids duplicate back-to-back markers.
    * Uses atomic write (temp file + rename) to prevent clients from seeing
      a half-written playlist.
    * Falls back to a simple append if parsing fails.
    """
    try:
        with open(playlist_path, 'r') as fh:
            lines = fh.readlines()

        # Skip if the last meaningful line is already a discontinuity tag.
        recent = [l.strip() for l in lines[-5:] if l.strip()]
        if recent and recent[-1] == '#EXT-X-DISCONTINUITY':
            logger.debug(f'Discontinuity already present in {playlist_path.name}')
            return

        # Find the last .ts segment line and insert right after it.
        insert_pos = len(lines)
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].strip().endswith('.ts'):
                insert_pos = i + 1
                break

        lines.insert(insert_pos, '#EXT-X-DISCONTINUITY\n')

        # Atomic write via temp file.
        tmp = playlist_path.with_suffix('.m3u8.tmp')
        with open(tmp, 'w') as fh:
            fh.writelines(lines)
        tmp.replace(playlist_path)

        logger.debug(
            f'Discontinuity injected in {playlist_path.name} at line {insert_pos}'
        )

    except Exception as e:
        logger.warning(
            f'Smart injection failed for {playlist_path.name}, falling back: {e}'
        )
        try:
            with open(playlist_path, 'a') as fh:
                fh.write('#EXT-X-DISCONTINUITY\n')
        except Exception as e2:
            logger.error(f'Fallback append also failed: {e2}')


# ---------------------------------------------------------------------------
# Background cleanup
# ---------------------------------------------------------------------------

def cleanup_stale_segments_loop(stop_event: threading.Event) -> None:
    """Daemon loop: delete ``.ts`` segments older than 3x the playlist window.

    Runs every 30 seconds.
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
                logger.debug(f'Cleaned up {deleted} stale segment(s)')
        except Exception as e:
            logger.warning(f'Error during stale segment cleanup: {e}')
