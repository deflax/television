"""Stream manager - coordinates FFmpeg and segment store.

This is the core component that handles stream transitions cleanly.
The key insight is to:
1. Wait for the current segment to finish writing (avoid truncated chunks)
2. Stop FFmpeg cleanly after the segment boundary
3. Mark discontinuity in segment store
4. Start new FFmpeg continuing from the next sequence number
"""

import asyncio
import logging
from enum import Enum, auto
from typing import Optional, Callable

from config import TRANSITION_TIMEOUT, HLS_SEGMENT_TIME
from segment_store import segment_store, setup_output_dirs
from ffmpeg_runner import FFmpegRunner

logger = logging.getLogger(__name__)

# Retry / backoff settings (shared between switch retries and crash recovery)
RETRY_MAX_ATTEMPTS = 5
RETRY_BACKOFF_BASE = 2.0  # seconds, doubles each attempt
RETRY_BACKOFF_MAX = 60.0  # maximum backoff cap


def _calc_backoff(attempt: int) -> float:
    """Calculate exponential backoff for a given attempt number (1-based)."""
    return min(RETRY_BACKOFF_BASE * (2 ** (attempt - 1)), RETRY_BACKOFF_MAX)


class StreamState(Enum):
    """State machine for stream manager."""
    IDLE = auto()
    STARTING = auto()
    RUNNING = auto()
    SWITCHING = auto()
    STOPPING = auto()


class StreamManager:
    """Manages stream lifecycle and transitions.
    
    Ensures clean segment boundaries at switch points by:
    1. Waiting for the current segment to finish writing
    2. Stopping FFmpeg cleanly (no truncated chunks)
    3. Marking discontinuity
    4. Starting new FFmpeg with next sequence number
    """
    
    def __init__(self):
        self._state = StreamState.IDLE
        self._ffmpeg: Optional[FFmpegRunner] = None
        self._current_url: Optional[str] = None
        self._lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._recovery_attempts = 0
    
    @property
    def state(self) -> StreamState:
        """Current stream state."""
        return self._state
    
    @property
    def current_url(self) -> Optional[str]:
        """Currently playing stream URL."""
        return self._current_url
    
    @property
    def is_running(self) -> bool:
        """Check if stream is actively running."""
        return self._state == StreamState.RUNNING
    
    async def _try_start_ffmpeg(self, url: str) -> bool:
        """Start a new FFmpeg process and wait for the first segment.
        
        Handles: directory setup, sequence numbering, stream probing,
        and first-segment confirmation. On failure, stops FFmpeg and
        sets state to IDLE.
        
        Caller must hold self._lock.
        Returns True if FFmpeg started and produced a segment.
        """
        setup_output_dirs()
        
        start_seq = await segment_store.get_next_sequence()
        
        self._ffmpeg = FFmpegRunner(on_segment=self._on_segment)
        
        if not await self._ffmpeg.start(url, start_seq):
            logger.error('Failed to start FFmpeg')
            self._state = StreamState.IDLE
            return False
        
        # Update segment store with detected stream info
        if self._ffmpeg.stream_info:
            info = self._ffmpeg.stream_info
            await segment_store.set_source_info(info.width, info.height, info.bitrate)
        
        # Wait for first segment to confirm stream is working
        if await self._ffmpeg.wait_for_segment(timeout=TRANSITION_TIMEOUT):
            return True
        
        logger.error('No segment produced within timeout')
        await self._ffmpeg.stop()
        self._state = StreamState.IDLE
        return False
    
    async def start(self, url: str) -> bool:
        """Start streaming from the given URL.
        
        Returns True if started successfully.
        """
        async with self._lock:
            return await self._start_unlocked(url)
    
    async def _start_unlocked(self, url: str) -> bool:
        """Start streaming (caller must hold lock)."""
        if self._state not in (StreamState.IDLE, StreamState.STOPPING):
            logger.warning(f'Cannot start in state {self._state}')
            return False
        
        self._stop_event.clear()
        self._state = StreamState.STARTING
        
        logger.info(f'Starting stream: {url[:60]}...')
        
        if await self._try_start_ffmpeg(url):
            self._current_url = url
            self._state = StreamState.RUNNING
            self._recovery_attempts = 0
            logger.info('Stream started successfully')
            return True
        
        return False
    
    async def switch(self, new_url: str) -> bool:
        """Switch to a new stream URL with clean transition.
        
        This is the core transition logic that ensures clean segment boundaries.
        Returns True if switch was successful.
        """
        async with self._lock:
            return await self._switch_unlocked(new_url)
    
    async def switch_with_retry(
        self,
        new_url: str,
        should_abort: Callable[[], bool] | None = None,
    ) -> bool:
        """Switch to a new stream URL, retrying with exponential backoff on failure.
        
        Args:
            new_url: HLS stream URL to switch to.
            should_abort: Optional callback checked before each retry. If it
                          returns True the retry loop exits early (e.g. when a
                          newer playhead change has arrived).
        
        Returns True if the switch eventually succeeded.
        """
        for attempt in range(1, RETRY_MAX_ATTEMPTS + 1):
            if should_abort and should_abort():
                logger.info('Switch retry aborted (superseded)')
                return False
            
            if await self.switch(new_url):
                return True
            
            if attempt == RETRY_MAX_ATTEMPTS:
                logger.error(
                    f'Switch failed after {RETRY_MAX_ATTEMPTS} attempts: '
                    f'{new_url[:60]}...'
                )
                return False
            
            backoff = _calc_backoff(attempt)
            logger.warning(
                f'Switch failed (attempt {attempt}/{RETRY_MAX_ATTEMPTS}), '
                f'retrying in {backoff:.1f}s...'
            )
            await asyncio.sleep(backoff)
        
        return False  # unreachable, but keeps type checker happy
    
    async def _switch_unlocked(self, new_url: str) -> bool:
        """Switch to a new stream URL (caller must hold lock)."""
        # Check state and decide action
        if self._state == StreamState.IDLE:
            return await self._start_unlocked(new_url)
        
        if self._state != StreamState.RUNNING:
            logger.warning(f'Cannot switch in state {self._state}')
            return False
        
        if new_url == self._current_url:
            logger.debug('Same URL, no switch needed')
            return True
        
        self._state = StreamState.SWITCHING
        
        logger.info(f'Switching stream to: {new_url[:60]}...')
        
        try:
            # Step 1: Stop current FFmpeg gracefully by sending 'q' to stdin.
            # The watcher is stopped first to prevent registering the truncated
            # last segment, then FFmpeg exits, and the runt segment is deleted.
            # This ensures only complete segments are in the store.
            logger.debug('Stopping current FFmpeg gracefully...')
            if self._ffmpeg:
                await self._ffmpeg.stop_graceful(timeout=float(HLS_SEGMENT_TIME + 5))
            
            # Step 2: Mark discontinuity in segment store
            await segment_store.mark_discontinuity()
            
            # Step 3-5: Start new FFmpeg and wait for first segment
            if await self._try_start_ffmpeg(new_url):
                self._current_url = new_url
                self._state = StreamState.RUNNING
                self._recovery_attempts = 0
                logger.info('Stream switch completed successfully')
                return True
            
            return False
                
        except Exception as e:
            logger.error(f'Error during stream switch: {e}', exc_info=True)
            self._state = StreamState.IDLE
            return False
    
    async def stop(self) -> None:
        """Stop the stream."""
        async with self._lock:
            await self._stop_unlocked()
    
    async def _stop_unlocked(self) -> None:
        """Stop the stream (caller must hold lock)."""
        if self._state == StreamState.IDLE:
            return
        
        self._state = StreamState.STOPPING
        logger.info('Stopping stream...')
        self._stop_event.set()
        
        if self._ffmpeg:
            await self._ffmpeg.stop()
            self._ffmpeg = None
        
        self._current_url = None
        self._state = StreamState.IDLE
        logger.info('Stream stopped')
    
    async def run_loop(self) -> None:
        """Main loop that monitors FFmpeg and handles crashes.
        
        Call this as a background task to auto-restart on crashes.
        """
        while not self._stop_event.is_set():
            try:
                await self._check_and_recover()
                await asyncio.sleep(1.0)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f'Error in stream manager loop: {e}')
                await asyncio.sleep(1.0)
    
    async def _check_and_recover(self) -> None:
        """Check FFmpeg health and attempt recovery if needed."""
        async with self._lock:
            if not self._ffmpeg or self._state != StreamState.RUNNING:
                return
            
            # Check if FFmpeg is still running
            if self._ffmpeg.is_running:
                return
            
            exit_code = await self._ffmpeg.wait()
            logger.warning(f'FFmpeg exited unexpectedly with code {exit_code}')
            
            # Try to restart with same URL
            if not self._current_url:
                self._state = StreamState.IDLE
                return
            
            # Exponential backoff before retry (capped at max)
            self._recovery_attempts += 1
            backoff = _calc_backoff(self._recovery_attempts)
            logger.info(
                f'Attempting crash recovery (attempt {self._recovery_attempts}, '
                f'backoff {backoff:.1f}s)...'
            )
            await asyncio.sleep(backoff)
            
            await segment_store.mark_discontinuity()
            
            if await self._try_start_ffmpeg(self._current_url):
                logger.info('Crash recovery successful')
                self._recovery_attempts = 0
                self._state = StreamState.RUNNING
            else:
                # _try_start_ffmpeg sets IDLE on failure, but we want to keep
                # retrying on the next loop iteration — restore RUNNING state
                self._state = StreamState.RUNNING
    
    async def _on_segment(self, variant: int, filename: str, duration: float) -> None:
        """Callback when FFmpeg produces a new segment."""
        await segment_store.add_segment(variant, filename, duration)


# Global stream manager instance
stream_manager = StreamManager()
