"""FFmpeg process runner with segment detection.

Runs FFmpeg and monitors its output directory for new segments,
registering them with the segment store as they appear.
"""

import asyncio
import logging
import re
import time
from pathlib import Path
from typing import Optional, Callable, Awaitable

from config import (
    HLS_OUTPUT_DIR, HLS_SEGMENT_TIME, HLS_LIST_SIZE,
    MUX_MODE, ABR_VARIANTS, ABR_PRESET, ABR_GOP_SIZE,
    ICECAST_ENABLED, ICECAST_HOST, ICECAST_PORT,
    ICECAST_SOURCE_PASSWORD, ICECAST_MOUNT,
    ICECAST_AUDIO_BITRATE, ICECAST_AUDIO_FORMAT,
    NUM_VARIANTS, SEGMENT_STABILITY_DELAY,
    parse_bitrate,
)
from utils import wait_for_stable_file

logger = logging.getLogger(__name__)

# Regex to extract segment number from filename
SEGMENT_PATTERN = re.compile(r'segment_(\d+)\.ts$')


def _build_icecast_output(cmd: list[str]) -> None:
    """Append Icecast audio-only output arguments to cmd."""
    if not ICECAST_ENABLED:
        return
    
    icecast_url = (
        f'icecast://source:{ICECAST_SOURCE_PASSWORD}'
        f'@{ICECAST_HOST}:{ICECAST_PORT}{ICECAST_MOUNT}'
    )
    
    if ICECAST_AUDIO_FORMAT == 'aac':
        cmd.extend([
            '-map', '0:a',
            '-c:a', 'aac',
            '-b:a', ICECAST_AUDIO_BITRATE,
            '-f', 'adts',
            '-content_type', 'audio/aac',
            icecast_url,
        ])
    else:
        cmd.extend([
            '-map', '0:a',
            '-c:a', 'libmp3lame',
            '-b:a', ICECAST_AUDIO_BITRATE,
            '-f', 'mp3',
            '-content_type', 'audio/mpeg',
            icecast_url,
        ])


def build_ffmpeg_command(input_url: str, start_number: int = 0) -> list[str]:
    """Build the FFmpeg command based on MUX_MODE."""
    if MUX_MODE == 'abr':
        return _build_abr_command(input_url, start_number)
    return _build_copy_command(input_url, start_number)


def _build_copy_command(input_url: str, start_number: int) -> list[str]:
    """Build FFmpeg command for copy/passthrough mode."""
    cmd = [
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
        f'{HLS_OUTPUT_DIR}/stream.m3u8',
    ]
    _build_icecast_output(cmd)
    return cmd


def _build_abr_command(input_url: str, start_number: int) -> list[str]:
    """Build FFmpeg command for ABR mode."""
    num_variants = len(ABR_VARIANTS)
    total_streams = num_variants + 1
    
    # Build filter complex
    split_outputs = ''.join(f'[v_{i}_in]' for i in range(num_variants))
    filter_parts = [f'[0:v]split={num_variants}{split_outputs}']
    
    for i, variant in enumerate(ABR_VARIANTS):
        h = variant['height']
        filter_parts.append(
            f"[v_{i}_in]scale=w=-2:h='min({h},ih)'"
            f":force_original_aspect_ratio=decrease[v_{i}]"
        )
    
    filter_complex = '; '.join(filter_parts)
    
    cmd = [
        'ffmpeg',
        '-y',
        '-re',
        '-i', input_url,
        '-filter_complex', filter_complex,
        '-map', '0:v',
        '-c:v:0', 'copy',
        '-map', '0:a',
        '-c:a:0', 'copy',
    ]
    
    # Add transcoded variants
    for i, variant in enumerate(ABR_VARIANTS):
        idx = i + 1
        vb = variant['video_bitrate']
        ab = variant['audio_bitrate']
        
        kbps = parse_bitrate(vb)
        maxrate = f'{int(kbps * 1.07)}k'
        bufsize = f'{int(kbps * 1.5)}k'
        
        cmd.extend([
            '-map', f'[v_{i}]',
            f'-c:v:{idx}', 'libx264',
            '-preset', ABR_PRESET,
            f'-b:v:{idx}', vb,
            f'-maxrate:v:{idx}', maxrate,
            f'-bufsize:v:{idx}', bufsize,
            f'-g:v:{idx}', str(ABR_GOP_SIZE),
            f'-sc_threshold:v:{idx}', '0',
            '-map', '0:a',
            f'-c:a:{idx}', 'aac',
            f'-b:a:{idx}', ab,
            f'-ac:a:{idx}', '2',
        ])
    
    # HLS output
    var_stream_map = ' '.join(f'v:{i},a:{i}' for i in range(total_streams))
    
    cmd.extend([
        '-f', 'hls',
        '-hls_time', str(HLS_SEGMENT_TIME),
        '-hls_list_size', str(HLS_LIST_SIZE),
        '-hls_flags', 'independent_segments+append_list+omit_endlist',
        '-hls_segment_type', 'mpegts',
        '-start_number', str(start_number),
        '-hls_segment_filename', f'{HLS_OUTPUT_DIR}/stream_%v/segment_%05d.ts',
        '-master_pl_name', 'stream.m3u8',
        '-var_stream_map', var_stream_map,
        f'{HLS_OUTPUT_DIR}/stream_%v/playlist.m3u8',
    ])
    
    _build_icecast_output(cmd)
    return cmd


class FFmpegRunner:
    """Manages FFmpeg process lifecycle and segment detection."""
    
    def __init__(
        self,
        on_segment: Optional[Callable[[int, str, float], Awaitable[None]]] = None,
    ):
        """
        Args:
            on_segment: Async callback called when new segment is detected.
                       Arguments: (variant_index, filename, duration)
        """
        self._process: Optional[asyncio.subprocess.Process] = None
        self._on_segment = on_segment
        self._running = False
        self._watcher_task: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None
        self._known_segments: set[str] = set()
    
    @property
    def is_running(self) -> bool:
        """Check if FFmpeg process is running."""
        return self._running and self._process is not None and self._process.returncode is None
    
    async def start(self, input_url: str, start_number: int = 0) -> bool:
        """Start FFmpeg with the given input URL.
        
        Returns True if started successfully.
        """
        if self._running:
            logger.warning('FFmpeg already running, stopping first')
            await self.stop()
        
        self._known_segments = self._scan_existing_segments()
        
        cmd = build_ffmpeg_command(input_url, start_number)
        
        logger.info(f'Starting FFmpeg: mode={MUX_MODE} start={start_number} url={input_url}')
        
        try:
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            self._running = True
            
            # Start background tasks
            self._watcher_task = asyncio.create_task(self._watch_segments())
            self._stderr_task = asyncio.create_task(self._drain_stderr())
            
            return True
            
        except Exception as e:
            logger.error(f'Failed to start FFmpeg: {e}')
            self._running = False
            return False
    
    async def stop(self, graceful_timeout: float = 5.0) -> Optional[int]:
        """Stop FFmpeg gracefully.
        
        Returns the exit code, or None if process wasn't running.
        """
        if not self._process:
            return None
        
        self._running = False
        
        # Cancel watcher task
        if self._watcher_task:
            self._watcher_task.cancel()
            try:
                await self._watcher_task
            except asyncio.CancelledError:
                pass
        
        # Terminate process
        exit_code = None
        if self._process.returncode is None:
            logger.info('Stopping FFmpeg...')
            self._process.terminate()
            
            try:
                exit_code = await asyncio.wait_for(
                    self._process.wait(),
                    timeout=graceful_timeout
                )
            except asyncio.TimeoutError:
                logger.warning('FFmpeg did not terminate, killing')
                self._process.kill()
                exit_code = await self._process.wait()
        else:
            exit_code = self._process.returncode
        
        # Wait for stderr drain
        if self._stderr_task:
            self._stderr_task.cancel()
            try:
                await self._stderr_task
            except asyncio.CancelledError:
                pass
        
        self._process = None
        logger.info(f'FFmpeg stopped with exit code {exit_code}')
        return exit_code
    
    async def wait(self) -> Optional[int]:
        """Wait for FFmpeg to exit.
        
        Returns the exit code.
        """
        if not self._process:
            return None
        return await self._process.wait()
    
    async def wait_for_segment(self, timeout: float = 15.0) -> bool:
        """Wait for at least one new segment to be produced.
        
        Returns True if a segment was produced within timeout.
        """
        start_count = len(self._known_segments)
        deadline = time.monotonic() + timeout
        
        while time.monotonic() < deadline:
            if len(self._known_segments) > start_count:
                return True
            if not self.is_running:
                return False
            await asyncio.sleep(0.5)
        
        return False
    
    def _scan_existing_segments(self) -> set[str]:
        """Scan output directory for existing segment files."""
        segments = set()
        output_path = Path(HLS_OUTPUT_DIR)
        
        for ts_file in output_path.rglob('segment_*.ts'):
            segments.add(str(ts_file))
        
        return segments
    
    async def _watch_segments(self) -> None:
        """Watch for new segments and notify callback."""
        output_path = Path(HLS_OUTPUT_DIR)
        
        while self._running:
            try:
                await asyncio.sleep(0.5)
                
                # Scan for new segments
                for variant in range(NUM_VARIANTS):
                    if NUM_VARIANTS > 1:
                        variant_path = output_path / f'stream_{variant}'
                    else:
                        variant_path = output_path
                    
                    if not variant_path.exists():
                        continue
                    
                    for ts_file in variant_path.glob('segment_*.ts'):
                        file_key = str(ts_file)
                        
                        if file_key in self._known_segments:
                            continue
                        
                        # Wait for file to stabilize
                        if not await wait_for_stable_file(ts_file, SEGMENT_STABILITY_DELAY):
                            continue
                        
                        self._known_segments.add(file_key)
                        
                        # Extract segment number and calculate duration
                        match = SEGMENT_PATTERN.search(ts_file.name)
                        if match:
                            # Use configured segment time as duration estimate
                            duration = float(HLS_SEGMENT_TIME)
                            
                            if self._on_segment:
                                await self._on_segment(variant, ts_file.name, duration)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f'Error watching segments: {e}')
    
    async def _drain_stderr(self) -> None:
        """Read and log FFmpeg stderr output."""
        if not self._process or not self._process.stderr:
            return
        
        try:
            while True:
                line = await self._process.stderr.readline()
                if not line:
                    break
                decoded = line.decode().rstrip()
                if decoded:
                    logger.debug(f'ffmpeg: {decoded}')
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug(f'Stderr drain error: {e}')
