"""FFmpeg process management."""

import logging
import subprocess
import threading
from typing import Optional

from config import (
    HLS_OUTPUT_DIR, HLS_SEGMENT_TIME, HLS_LIST_SIZE,
    MUX_MODE, ABR_PRESET, ABR_GOP_SIZE, ABR_VARIANTS,
    ICECAST_ENABLED, ICECAST_HOST, ICECAST_PORT, ICECAST_SOURCE_PASSWORD,
    ICECAST_MOUNT, ICECAST_AUDIO_BITRATE, ICECAST_AUDIO_FORMAT
)

logger = logging.getLogger(__name__)


def parse_bitrate(bitrate_str: str) -> int:
    """Parse bitrate string (e.g., '5000k', '5M') to integer kbps."""
    bitrate_str = bitrate_str.lower().strip()
    if bitrate_str.endswith('m'):
        return int(float(bitrate_str[:-1]) * 1000)
    elif bitrate_str.endswith('k'):
        return int(float(bitrate_str[:-1]))
    return int(bitrate_str)


def build_copy_cmd(input_url: str, start_number: int = 0) -> list[str]:
    """Build ffmpeg command for copy/passthrough mode (single stream, no transcoding)."""
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
        f'{HLS_OUTPUT_DIR}/stream.m3u8'
    ]
    
    # Add audio-only output to Icecast if enabled
    if ICECAST_ENABLED:
        icecast_url = f'icecast://source:{ICECAST_SOURCE_PASSWORD}@{ICECAST_HOST}:{ICECAST_PORT}{ICECAST_MOUNT}'
        
        if ICECAST_AUDIO_FORMAT == 'aac':
            cmd.extend([
                '-map', '0:a',
                '-c:a', 'aac',
                '-b:a', ICECAST_AUDIO_BITRATE,
                '-f', 'adts',
                '-content_type', 'audio/aac',
                icecast_url
            ])
        else:  # mp3 (default)
            cmd.extend([
                '-map', '0:a',
                '-c:a', 'libmp3lame',
                '-b:a', ICECAST_AUDIO_BITRATE,
                '-f', 'mp3',
                '-content_type', 'audio/mpeg',
                icecast_url
            ])
    
    return cmd


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
    
    # Add audio-only output to Icecast if enabled
    if ICECAST_ENABLED:
        icecast_url = f'icecast://source:{ICECAST_SOURCE_PASSWORD}@{ICECAST_HOST}:{ICECAST_PORT}{ICECAST_MOUNT}'
        
        if ICECAST_AUDIO_FORMAT == 'aac':
            cmd.extend([
                '-map', '0:a',
                '-c:a', 'aac',
                '-b:a', ICECAST_AUDIO_BITRATE,
                '-f', 'adts',
                '-content_type', 'audio/aac',
                icecast_url
            ])
        else:  # mp3 (default)
            cmd.extend([
                '-map', '0:a',
                '-c:a', 'libmp3lame',
                '-b:a', ICECAST_AUDIO_BITRATE,
                '-f', 'mp3',
                '-content_type', 'audio/mpeg',
                icecast_url
            ])
    
    return cmd


class FFmpegManager:
    """Manages ffmpeg process lifecycle."""
    
    def __init__(self, segment_counter_lock: threading.Lock):
        self.process: Optional[subprocess.Popen] = None
        self.segment_counter = 0
        self.segment_counter_lock = segment_counter_lock
    
    def start(self, input_url: str) -> subprocess.Popen:
        """Start ffmpeg based on configured MUX_MODE."""
        with self.segment_counter_lock:
            start_num = self.segment_counter
        
        if MUX_MODE == 'abr':
            cmd = build_abr_cmd(input_url, start_number=start_num)
            variant_desc = ', '.join(f"{v['height']}p" for v in ABR_VARIANTS)
            mode_desc = f"ABR (source + {variant_desc})"
        else:
            cmd = build_copy_cmd(input_url, start_number=start_num)
            mode_desc = "copy (passthrough)"
        
        logger.info(f"Starting ffmpeg [{mode_desc}] segment={start_num} input={input_url}")
        
        self.process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE
        )
        
        # Monitor ffmpeg stderr in background
        def log_stderr():
            if self.process and self.process.stderr:
                for line in self.process.stderr:
                    decoded = line.decode().rstrip()
                    if decoded:
                        logger.debug(f"ffmpeg: {decoded}")
        
        stderr_thread = threading.Thread(target=log_stderr, daemon=True)
        stderr_thread.start()
        
        return self.process
    
    def stop(self, get_next_segment_number_func):
        """Stop the current ffmpeg process and update segment counter."""
        if self.process:
            logger.info("Stopping ffmpeg...")
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
            self.process = None
            
            # Update segment counter from written files
            next_num = get_next_segment_number_func()
            with self.segment_counter_lock:
                self.segment_counter = next_num
            logger.debug(f"Segment counter updated to {next_num}")
    
    def is_running(self) -> bool:
        """Check if ffmpeg process is still running."""
        return self.process is not None and self.process.poll() is None
    
    def get_exit_code(self) -> Optional[int]:
        """Get ffmpeg exit code if process has exited."""
        if self.process:
            return self.process.poll()
        return None
