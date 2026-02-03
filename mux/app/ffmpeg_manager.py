"""FFmpeg process lifecycle management.

Owns starting / stopping ffmpeg processes and tracking the continuous
segment counter that bridges across stream transitions.
"""

import logging
import subprocess
import threading
from typing import Callable, Optional

from config import MUX_MODE, ABR_VARIANTS
from ffmpeg_command import build_copy_cmd, build_abr_cmd

logger = logging.getLogger(__name__)


class FFmpegManager:
    """Manages a single ffmpeg process and its segment counter.

    The *segment_counter* is shared state protected by an external lock so
    that multiple ``FFmpegManager`` instances (old / new during a transition)
    can safely read / update it.
    """

    def __init__(self, segment_counter_lock: threading.Lock):
        self.process: Optional[subprocess.Popen] = None
        self.segment_counter: int = 0
        self.segment_counter_lock = segment_counter_lock

    # ------------------------------------------------------------------
    # Segment counter helpers
    # ------------------------------------------------------------------

    def get_segment_counter(self) -> int:
        """Thread-safe read of the current segment counter."""
        with self.segment_counter_lock:
            return self.segment_counter

    def set_segment_counter(self, value: int) -> None:
        """Thread-safe write of the segment counter."""
        with self.segment_counter_lock:
            self.segment_counter = value

    # ------------------------------------------------------------------
    # Process lifecycle
    # ------------------------------------------------------------------

    def start(self, input_url: str) -> subprocess.Popen:
        """Start ffmpeg for the configured MUX_MODE.

        Returns the ``Popen`` handle.
        """
        start_num = self.get_segment_counter()

        if MUX_MODE == 'abr':
            cmd = build_abr_cmd(input_url, start_number=start_num)
            variant_desc = ', '.join(f"{v['height']}p" for v in ABR_VARIANTS)
            mode_desc = f'ABR (source + {variant_desc})'
        else:
            cmd = build_copy_cmd(input_url, start_number=start_num)
            mode_desc = 'copy (passthrough)'

        logger.info(
            f'Starting ffmpeg [{mode_desc}] segment={start_num} input={input_url}'
        )

        self.process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

        self._start_stderr_logger()
        return self.process

    def stop(self, get_next_segment_number: Callable[[], int]) -> None:
        """Gracefully stop ffmpeg and update the segment counter.

        *get_next_segment_number* is called after the process exits to scan
        the output directory and determine the next segment number.
        """
        if self.process is None:
            return

        logger.info('Stopping ffmpeg...')
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
        self.process = None

        next_num = get_next_segment_number()
        self.set_segment_counter(next_num)
        logger.debug(f'Segment counter updated to {next_num}')

    def is_running(self) -> bool:
        """Return ``True`` if the ffmpeg process is alive."""
        return self.process is not None and self.process.poll() is None

    def get_exit_code(self) -> Optional[int]:
        """Return the exit code, or ``None`` if the process hasn't exited."""
        if self.process:
            return self.process.poll()
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _start_stderr_logger(self) -> None:
        """Drain ffmpeg stderr to the logger in a daemon thread."""
        proc = self.process

        def _drain():
            if proc and proc.stderr:
                for line in proc.stderr:
                    decoded = line.decode().rstrip()
                    if decoded:
                        logger.debug(f'ffmpeg: {decoded}')

        threading.Thread(target=_drain, daemon=True).start()
