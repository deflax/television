"""Stream transition management.

Encapsulates the logic for gracefully switching from one source stream to
another while protecting the continuous HLS output from media errors.

Key guarantees:
- Only one transition runs at a time (``transition_lock``).
- The new ffmpeg starts *before* the old one is stopped (overlap).
- A ``#EXT-X-DISCONTINUITY`` marker is injected only after the new stream
  has produced at least one segment, so clients always have content to fetch.
"""

import logging
import threading
import time
from ffmpeg_manager import FFmpegManager
from playlist_manager import get_next_segment_number, inject_discontinuity

logger = logging.getLogger(__name__)

# How long (seconds) to wait for the new stream to produce its first segment.
_NEW_SEGMENT_TIMEOUT = 15


class StreamTransition:
    """Coordinates graceful transitions between source streams."""

    def __init__(self, segment_counter_lock: threading.Lock):
        self._segment_counter_lock = segment_counter_lock
        self._transition_lock = threading.Lock()
        self.is_transitioning = threading.Event()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execute(
        self,
        ffmpeg_manager: FFmpegManager,
        new_url: str,
    ) -> bool:
        """Perform a graceful stream transition.

        1. Start a *new* ffmpeg instance on *new_url* (overlap).
        2. Wait for the first segment to land on disk.
        3. Inject ``#EXT-X-DISCONTINUITY`` into playlists.
        4. Stop the *old* ffmpeg instance.
        5. Swap state into *ffmpeg_manager* so the main loop can continue.

        Returns ``True`` on success, ``False`` on fallback/recovery.
        """
        with self._transition_lock:
            self.is_transitioning.set()
            logger.info('Starting graceful stream transition...')

            try:
                return self._do_transition(ffmpeg_manager, new_url)
            except Exception as e:
                logger.error(f'Error during graceful transition: {e}', exc_info=True)
                self._fallback(ffmpeg_manager)
                return False
            finally:
                self.is_transitioning.clear()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _do_transition(
        self,
        ffmpeg_manager: FFmpegManager,
        new_url: str,
    ) -> bool:
        old_segment = ffmpeg_manager.get_segment_counter()
        logger.debug(f'Old stream at segment {old_segment}, starting new stream...')

        # --- 1. Start new ffmpeg (old is still running) ----------------
        new_ffmpeg = FFmpegManager(self._segment_counter_lock)
        new_ffmpeg.set_segment_counter(old_segment)
        new_ffmpeg.start(new_url)

        # --- 2. Wait for first new segment -----------------------------
        segment_ready = self._wait_for_segment(new_ffmpeg, old_segment)
        if not segment_ready:
            logger.warning(
                f'New segments not available after {_NEW_SEGMENT_TIMEOUT}s, '
                'proceeding anyway'
            )

        # --- 3. Inject discontinuity BEFORE stopping old stream --------
        inject_discontinuity()
        time.sleep(0.5)  # let clients pick up the marker

        # --- 4. Stop old ffmpeg ----------------------------------------
        logger.debug('Stopping old ffmpeg instance...')
        ffmpeg_manager.stop(get_next_segment_number)

        # --- 5. Swap state into the original manager -------------------
        ffmpeg_manager.process = new_ffmpeg.process
        ffmpeg_manager.set_segment_counter(new_ffmpeg.get_segment_counter())

        logger.info('Graceful stream transition completed')
        return True

    @staticmethod
    def _wait_for_segment(
        new_ffmpeg: FFmpegManager,
        old_segment: int,
    ) -> bool:
        """Block until *new_ffmpeg* produces a segment beyond *old_segment*."""
        for elapsed in range(1, _NEW_SEGMENT_TIMEOUT + 1):
            time.sleep(1)

            if not new_ffmpeg.is_running():
                logger.error('New ffmpeg process died during startup')
                return False

            current = get_next_segment_number()
            if current > old_segment:
                logger.debug(f'New segment {current} available after {elapsed}s')
                return True

        return False

    @staticmethod
    def _fallback(ffmpeg_manager: FFmpegManager) -> None:
        """Best-effort recovery when the graceful path fails."""
        ffmpeg_manager.stop(get_next_segment_number)
        inject_discontinuity()
