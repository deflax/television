from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

from .config import ProducerConfig


@dataclass(frozen=True, slots=True)
class PlaybackQueue:
    files: tuple[Path, ...]

    @property
    def has_media(self) -> bool:
        return bool(self.files)


def generate_playback_queue(config: ProducerConfig) -> PlaybackQueue:
    files = list(_discover_media_files(config))
    rng = random.Random(config.shuffle_seed) if config.shuffle_seed is not None else random.Random()
    rng.shuffle(files)
    return PlaybackQueue(files=tuple(files))


def _discover_media_files(config: ProducerConfig) -> tuple[Path, ...]:
    supported_extensions = {extension.lower() for extension in config.supported_extensions}
    try:
        entries = sorted(config.media_dir.iterdir(), key=lambda path: str(path))
    except (FileNotFoundError, NotADirectoryError, PermissionError):
        return ()

    media_files: list[Path] = []
    for entry in entries:
        if entry.suffix.lower() not in supported_extensions:
            continue
        try:
            if not entry.is_file():
                continue
            media_files.append(entry.resolve(strict=True))
        except (FileNotFoundError, OSError):
            continue
    return tuple(media_files)
