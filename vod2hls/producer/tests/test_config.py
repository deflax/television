from importlib import import_module
from pathlib import Path
from collections.abc import Mapping
from typing import Protocol, cast

import pytest


class ProducerConfigLike(Protocol):
    media_dir: Path
    hls_dir: Path
    hls_playlist: str
    segment_seconds: int
    playlist_size: int
    rescan_seconds: int
    output_width: int
    output_height: int
    output_fps: int
    supported_extensions: tuple[str, ...]
    shuffle_seed: int | None
    log_level: str

    @property
    def hls_playlist_path(self) -> Path:
        ...


class ProducerConfigModule(Protocol):
    ProducerConfig: type[ProducerConfigLike]

    def load_config(self, env: Mapping[str, str] | None = None) -> ProducerConfigLike:
        ...


config_module = cast(ProducerConfigModule, cast(object, import_module("app.config")))
ProducerConfig = config_module.ProducerConfig
load_config = config_module.load_config


def test_default_config_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEDIA_DIR", raising=False)
    monkeypatch.delenv("HLS_DIR", raising=False)
    monkeypatch.delenv("HLS_PLAYLIST", raising=False)
    monkeypatch.delenv("SEGMENT_SECONDS", raising=False)
    monkeypatch.delenv("PLAYLIST_SIZE", raising=False)
    monkeypatch.delenv("RESCAN_SECONDS", raising=False)
    monkeypatch.delenv("OUTPUT_WIDTH", raising=False)
    monkeypatch.delenv("OUTPUT_HEIGHT", raising=False)
    monkeypatch.delenv("OUTPUT_FPS", raising=False)
    monkeypatch.delenv("SUPPORTED_EXTENSIONS", raising=False)
    monkeypatch.delenv("SHUFFLE_SEED", raising=False)
    monkeypatch.delenv("LOG_LEVEL", raising=False)

    config = load_config()

    assert config == ProducerConfig()
    assert config.hls_playlist_path == Path("/hls/live.m3u8")


def test_env_overrides_and_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEDIA_DIR", "/custom/media")
    monkeypatch.setenv("HLS_DIR", "/custom/hls")
    monkeypatch.setenv("HLS_PLAYLIST", "channel.m3u8")
    monkeypatch.setenv("SEGMENT_SECONDS", "12")
    monkeypatch.setenv("PLAYLIST_SIZE", "9")
    monkeypatch.setenv("RESCAN_SECONDS", "45")
    monkeypatch.setenv("OUTPUT_WIDTH", "1280")
    monkeypatch.setenv("OUTPUT_HEIGHT", "720")
    monkeypatch.setenv("OUTPUT_FPS", "24")
    monkeypatch.setenv("SUPPORTED_EXTENSIONS", ".MP4, mkv, .Mov")
    monkeypatch.setenv("SHUFFLE_SEED", "99")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    config = load_config()

    assert config.media_dir == Path("/custom/media")
    assert config.hls_dir == Path("/custom/hls")
    assert config.hls_playlist == "channel.m3u8"
    assert config.segment_seconds == 12
    assert config.playlist_size == 9
    assert config.rescan_seconds == 45
    assert config.output_width == 1280
    assert config.output_height == 720
    assert config.output_fps == 24
    assert config.supported_extensions == (".mp4", ".mkv", ".mov")
    assert config.shuffle_seed == 99
    assert config.log_level == "DEBUG"
    assert config.hls_playlist_path == Path("/custom/hls/channel.m3u8")


@pytest.mark.parametrize(
    ("env_name", "value"),
    [
        ("SEGMENT_SECONDS", "abc"),
        ("PLAYLIST_SIZE", "1.5"),
        ("RESCAN_SECONDS", ""),
        ("OUTPUT_WIDTH", "wide"),
        ("OUTPUT_HEIGHT", "tall"),
        ("OUTPUT_FPS", "fast"),
        ("SHUFFLE_SEED", "seed"),
    ],
)
def test_invalid_numeric_env_vars_raise_value_error(
    monkeypatch: pytest.MonkeyPatch,
    env_name: str,
    value: str,
) -> None:
    monkeypatch.setenv(env_name, value)

    with pytest.raises(ValueError, match=env_name):
        _ = load_config()


def test_supported_extensions_normalize_case_and_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPPORTED_EXTENSIONS", "MP4,.MkV, webm,AVI")

    config = load_config()

    assert config.supported_extensions == (".mp4", ".mkv", ".webm", ".avi")


def test_shuffle_seed_is_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SHUFFLE_SEED", raising=False)

    config = load_config()

    assert config.shuffle_seed is None
