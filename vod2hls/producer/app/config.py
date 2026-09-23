from __future__ import annotations

import os
from dataclasses import dataclass
from collections.abc import Mapping
from pathlib import Path


DEFAULT_MEDIA_DIR = Path("/media")
DEFAULT_HLS_DIR = Path("/hls")
DEFAULT_HLS_PLAYLIST = "live.m3u8"
DEFAULT_SEGMENT_SECONDS = 4
DEFAULT_PLAYLIST_SIZE = 6
DEFAULT_RESCAN_SECONDS = 30
DEFAULT_OUTPUT_WIDTH = 1920
DEFAULT_OUTPUT_HEIGHT = 1080
DEFAULT_OUTPUT_FPS = 30
DEFAULT_SUPPORTED_EXTENSIONS = (
    ".mp4",
    ".mkv",
    ".mov",
    ".avi",
    ".webm",
    ".m4v",
)
DEFAULT_LOG_LEVEL = "INFO"


@dataclass(frozen=True, slots=True)
class ProducerConfig:
    media_dir: Path = DEFAULT_MEDIA_DIR
    hls_dir: Path = DEFAULT_HLS_DIR
    hls_playlist: str = DEFAULT_HLS_PLAYLIST
    segment_seconds: int = DEFAULT_SEGMENT_SECONDS
    playlist_size: int = DEFAULT_PLAYLIST_SIZE
    rescan_seconds: int = DEFAULT_RESCAN_SECONDS
    output_width: int = DEFAULT_OUTPUT_WIDTH
    output_height: int = DEFAULT_OUTPUT_HEIGHT
    output_fps: int = DEFAULT_OUTPUT_FPS
    supported_extensions: tuple[str, ...] = DEFAULT_SUPPORTED_EXTENSIONS
    shuffle_seed: int | None = None
    log_level: str = DEFAULT_LOG_LEVEL

    @property
    def hls_playlist_path(self) -> Path:
        return self.hls_dir / self.hls_playlist


def load_config(env: Mapping[str, str] | None = None) -> ProducerConfig:
    source = os.environ if env is None else env
    return ProducerConfig(
        media_dir=Path(source.get("MEDIA_DIR", str(DEFAULT_MEDIA_DIR))),
        hls_dir=Path(source.get("HLS_DIR", str(DEFAULT_HLS_DIR))),
        hls_playlist=source.get("HLS_PLAYLIST", DEFAULT_HLS_PLAYLIST),
        segment_seconds=_parse_int(source, "SEGMENT_SECONDS", DEFAULT_SEGMENT_SECONDS),
        playlist_size=_parse_int(source, "PLAYLIST_SIZE", DEFAULT_PLAYLIST_SIZE),
        rescan_seconds=_parse_int(source, "RESCAN_SECONDS", DEFAULT_RESCAN_SECONDS),
        output_width=_parse_int(source, "OUTPUT_WIDTH", DEFAULT_OUTPUT_WIDTH),
        output_height=_parse_int(source, "OUTPUT_HEIGHT", DEFAULT_OUTPUT_HEIGHT),
        output_fps=_parse_int(source, "OUTPUT_FPS", DEFAULT_OUTPUT_FPS),
        supported_extensions=_parse_extensions(
            source.get("SUPPORTED_EXTENSIONS", ",".join(DEFAULT_SUPPORTED_EXTENSIONS))
        ),
        shuffle_seed=_parse_optional_int(source, "SHUFFLE_SEED"),
        log_level=source.get("LOG_LEVEL", DEFAULT_LOG_LEVEL),
    )


def _parse_int(source: Mapping[str, str], name: str, default: int) -> int:
    value = source.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"{name} must be an integer") from None


def _parse_optional_int(source: Mapping[str, str], name: str) -> int | None:
    value = source.get(name)
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"{name} must be an integer") from None


def _parse_extensions(value: str) -> tuple[str, ...]:
    extensions: list[str] = []
    for raw_extension in value.split(","):
        extension = raw_extension.strip().lower()
        if not extension:
            continue
        if not extension.startswith("."):
            extension = f".{extension.lstrip('.')}"
        extensions.append(extension)
    return tuple(extensions)
