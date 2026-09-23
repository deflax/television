from importlib import import_module
from pathlib import Path
from typing import Protocol, cast

import pytest


class ProducerConfigLike(Protocol):
    media_dir: Path
    supported_extensions: tuple[str, ...]
    shuffle_seed: int | None


class ProducerConfigFactory(Protocol):
    def __call__(
        self,
        *,
        media_dir: Path,
        shuffle_seed: int | None = None,
    ) -> ProducerConfigLike:
        ...


class ProducerConfigModule(Protocol):
    ProducerConfig: ProducerConfigFactory


class PlaybackQueueLike(Protocol):
    files: tuple[Path, ...]

    @property
    def has_media(self) -> bool:
        ...


class QueueModule(Protocol):
    def generate_playback_queue(self, config: ProducerConfigLike) -> PlaybackQueueLike:
        ...


config_module = cast(ProducerConfigModule, cast(object, import_module("app.config")))
ProducerConfig = config_module.ProducerConfig
queue_module = cast(QueueModule, cast(object, import_module("app.queue")))
generate_playback_queue = queue_module.generate_playback_queue


def touch(path: Path) -> Path:
    _ = path.write_bytes(b"media")
    return path


def test_queue_includes_supported_extensions_case_insensitively(tmp_path: Path) -> None:
    mp4_file = touch(tmp_path / "clip.MP4")
    mkv_file = touch(tmp_path / "movie.mKv")
    mov_file = touch(tmp_path / "scene.MOV")
    _ = touch(tmp_path / "notes.txt")

    queue = generate_playback_queue(ProducerConfig(media_dir=tmp_path, shuffle_seed=1))

    assert set(queue.files) == {mp4_file.resolve(), mkv_file.resolve(), mov_file.resolve()}
    assert queue.has_media is True


def test_queue_skips_directories_and_unsupported_files(tmp_path: Path) -> None:
    supported_file = touch(tmp_path / "video.webm")
    (tmp_path / "directory.mp4").mkdir()
    _ = touch(tmp_path / "archive.zip")

    queue = generate_playback_queue(ProducerConfig(media_dir=tmp_path, shuffle_seed=1))

    assert queue.files == (supported_file.resolve(),)


def test_empty_directory_returns_no_media(tmp_path: Path) -> None:
    queue = generate_playback_queue(ProducerConfig(media_dir=tmp_path))

    assert queue.files == ()
    assert queue.has_media is False


def test_missing_media_directory_returns_no_media(tmp_path: Path) -> None:
    queue = generate_playback_queue(ProducerConfig(media_dir=tmp_path / "missing"))
    assert queue.files == ()
    assert queue.has_media is False


def test_queue_preserves_spaces_quotes_unicode(tmp_path: Path) -> None:
    filenames = [
        "space name.mp4",
        "quote ' name.mkv",
        'double " quote.mov',
        "unicode-cafe-é-webm.webm",
        "日本語.m4v",
    ]
    expected_files = {touch(tmp_path / filename).resolve() for filename in filenames}

    queue = generate_playback_queue(ProducerConfig(media_dir=tmp_path, shuffle_seed=3))

    actual_files = set(queue.files)

    assert actual_files == expected_files
    assert all(isinstance(file_path, Path) for file_path in queue.files)
    assert all(file_path.is_absolute() for file_path in queue.files)


def test_shuffle_seed_makes_queue_order_deterministic(tmp_path: Path) -> None:
    for index in range(8):
        _ = touch(tmp_path / f"video-{index}.mp4")

    first_queue = generate_playback_queue(ProducerConfig(media_dir=tmp_path, shuffle_seed=42))
    second_queue = generate_playback_queue(ProducerConfig(media_dir=tmp_path, shuffle_seed=42))
    different_seed_queue = generate_playback_queue(ProducerConfig(media_dir=tmp_path, shuffle_seed=7))

    assert first_queue.files == second_queue.files
    assert first_queue.files != tuple(sorted(first_queue.files, key=lambda path: str(path)))
    assert different_seed_queue.files != first_queue.files


def test_unseeded_shuffle_uses_random_shuffle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for index in range(3):
        _ = touch(tmp_path / f"video-{index}.mp4")

    shuffle_calls: list[list[Path]] = []

    class RecordingRandom:
        def shuffle(self, files: list[Path]) -> None:
            shuffle_calls.append(list(files))
            files.reverse()

    def build_recording_random(seed: int | None = None) -> RecordingRandom:
        _ = seed
        return RecordingRandom()

    monkeypatch.setattr("app.queue.random.Random", build_recording_random)

    queue = generate_playback_queue(ProducerConfig(media_dir=tmp_path))

    sorted_files = tuple(sorted(queue.files, key=lambda path: str(path)))

    assert shuffle_calls
    assert queue.files == tuple(reversed(sorted_files))


def test_deleted_file_is_skipped_during_queue_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deleted_file = touch(tmp_path / "deleted.mp4")
    kept_file = touch(tmp_path / "kept.mp4")
    original_is_file = Path.is_file

    def deleting_is_file(path: Path) -> bool:
        if path == deleted_file:
            deleted_file.unlink()
        return original_is_file(path)

    monkeypatch.setattr(Path, "is_file", deleting_is_file)

    queue = generate_playback_queue(ProducerConfig(media_dir=tmp_path, shuffle_seed=1))

    assert queue.files == (kept_file.resolve(),)
