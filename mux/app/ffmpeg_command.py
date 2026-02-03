"""FFmpeg command builders for HLS output modes.

Constructs ffmpeg argument lists for copy (passthrough) and ABR (adaptive
bitrate) modes.  Icecast audio output is appended when enabled.
"""

import logging

from config import (
    HLS_OUTPUT_DIR, HLS_SEGMENT_TIME, HLS_LIST_SIZE,
    ABR_PRESET, ABR_GOP_SIZE, ABR_VARIANTS,
    ICECAST_ENABLED, ICECAST_HOST, ICECAST_PORT,
    ICECAST_SOURCE_PASSWORD, ICECAST_MOUNT,
    ICECAST_AUDIO_BITRATE, ICECAST_AUDIO_FORMAT,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_bitrate(bitrate_str: str) -> int:
    """Parse a human-readable bitrate string to integer kbps.

    Examples: '5000k' -> 5000, '5M' -> 5000, '128' -> 128
    """
    bitrate_str = bitrate_str.lower().strip()
    if bitrate_str.endswith('m'):
        return int(float(bitrate_str[:-1]) * 1000)
    if bitrate_str.endswith('k'):
        return int(float(bitrate_str[:-1]))
    return int(bitrate_str)


def _build_icecast_output(cmd: list[str]) -> None:
    """Append Icecast audio-only output arguments to *cmd* in-place."""
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
    else:  # mp3 (default)
        cmd.extend([
            '-map', '0:a',
            '-c:a', 'libmp3lame',
            '-b:a', ICECAST_AUDIO_BITRATE,
            '-f', 'mp3',
            '-content_type', 'audio/mpeg',
            icecast_url,
        ])


# ---------------------------------------------------------------------------
# Public builders
# ---------------------------------------------------------------------------

def build_copy_cmd(input_url: str, start_number: int = 0) -> list[str]:
    """Build ffmpeg command for copy/passthrough mode (single stream)."""
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


def build_abr_cmd(input_url: str, start_number: int = 0) -> list[str]:
    """Build ffmpeg command for ABR mode (source copy + transcoded variants).

    Smart scaling: only transcodes resolutions at or below source height.
    Stream 0 is always a direct passthrough of the source.
    """
    num_variants = len(ABR_VARIANTS)
    total_streams = num_variants + 1  # +1 for source passthrough

    # --- filter_complex: split + scale for each variant ---------------
    split_outputs = ''.join(f'[v_{i}_in]' for i in range(num_variants))
    filter_parts = [f'[0:v]split={num_variants}{split_outputs}']

    for i, variant in enumerate(ABR_VARIANTS):
        h = variant['height']
        filter_parts.append(
            f"[v_{i}_in]scale=w=-2:h='min({h},ih)'"
            f":force_original_aspect_ratio=decrease[v_{i}]"
        )

    filter_complex = '; '.join(filter_parts)

    # --- base command -------------------------------------------------
    cmd = [
        'ffmpeg',
        '-y',
        '-re',
        '-i', input_url,
        '-filter_complex', filter_complex,
        # Stream 0: source passthrough (not from filter graph)
        '-map', '0:v',
        '-c:v:0', 'copy',
        '-map', '0:a',
        '-c:a:0', 'copy',
    ]

    # --- transcoded variants ------------------------------------------
    for i, variant in enumerate(ABR_VARIANTS):
        idx = i + 1  # 0 is source passthrough
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

    # --- HLS muxer output ---------------------------------------------
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
