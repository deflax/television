from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import cast

from .config import ProducerConfig


AudioProbe = Callable[[Path], bool]

SILENT_AUDIO_SOURCE = "anullsrc=channel_layout=stereo:sample_rate=48000"


def input_has_audio(input_path: Path) -> bool:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=index",
            "-of",
            "json",
            str(input_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    probe = cast(dict[str, object], json.loads(result.stdout or "{}"))
    streams = cast(list[object] | None, probe.get("streams"))
    return isinstance(streams, list) and len(streams) > 0


def build_ffmpeg_command(
    input_path: Path,
    config: ProducerConfig,
    audio_probe: AudioProbe = input_has_audio,
) -> list[str]:
    has_audio = audio_probe(input_path)
    gop_size = config.segment_seconds * config.output_fps
    segment_pattern = config.hls_dir / "live-%09d.ts"
    video_filter = _scale_pad_filter(config)

    command = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-y",
        "-re",
        "-i",
        str(input_path),
    ]

    if has_audio:
        command.extend(["-map", "0:v:0", "-map", "0:a:0"])
    else:
        command.extend(["-f", "lavfi", "-i", SILENT_AUDIO_SOURCE])
        command.extend(["-map", "0:v:0", "-map", "1:a:0", "-shortest"])

    command.extend(
        [
            "-vf",
            video_filter,
            "-r",
            str(config.output_fps),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-profile:v",
            "high",
            "-pix_fmt",
            "yuv420p",
            "-b:v",
            "4000k",
            "-g",
            str(gop_size),
            "-keyint_min",
            str(gop_size),
            "-sc_threshold",
            "0",
            "-force_key_frames",
            f"expr:gte(t,n_forced*{config.segment_seconds})",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-f",
            "hls",
            "-hls_time",
            str(config.segment_seconds),
            "-hls_list_size",
            str(config.playlist_size),
            "-hls_flags",
            "delete_segments+append_list+discont_start+omit_endlist+temp_file",
            "-hls_segment_filename",
            str(segment_pattern),
            str(config.hls_playlist_path),
        ]
    )

    return command


def _scale_pad_filter(config: ProducerConfig) -> str:
    width = config.output_width
    height = config.output_height
    return (
        f"scale=w={width}:h={height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1"
    )
