from __future__ import annotations

import json
import os
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from importlib import import_module
from pathlib import Path
from typing import Protocol, cast

import pytest


class ProducerConfigLike(Protocol):
    hls_dir: Path
    hls_playlist: str

    @property
    def hls_playlist_path(self) -> Path:
        ...


class ConfigModuleLike(Protocol):
    class ProducerConfigFactory(Protocol):
        def __call__(self, *, hls_dir: Path, hls_playlist: str = ...) -> ProducerConfigLike:
            ...

    ProducerConfig: ProducerConfigFactory


class HealthModuleLike(Protocol):
    def write_status(
        self,
        config: ProducerConfigLike,
        state: str,
        message: str,
        *,
        current_file: Path | str | None = None,
        played_files: Sequence[Path | str] | None = None,
        pending_files: Sequence[Path | str] | None = None,
        updated_at: datetime | None = None,
    ) -> Path:
        ...

    def read_status(self, config: ProducerConfigLike) -> dict[str, object] | None:
        ...

    def evaluate_health(
        self,
        config: ProducerConfigLike,
        *,
        max_age_seconds: int = ...,
    ) -> tuple[bool, str]:
        ...

    def healthcheck(self, config: ProducerConfigLike, *, max_age_seconds: int = ...) -> bool:
        ...

    def main(self, argv: list[str] | None = None) -> int:
        ...


config_module = cast(ConfigModuleLike, cast(object, import_module("app.config")))
health_module = cast(HealthModuleLike, cast(object, import_module("app.health")))
ProducerConfig = config_module.ProducerConfig

write_status = health_module.write_status
read_status = health_module.read_status
evaluate_health = health_module.evaluate_health
healthcheck = health_module.healthcheck
main = health_module.main


def make_config(tmp_path: Path) -> ProducerConfigLike:
    return ProducerConfig(hls_dir=tmp_path / "hls")


def touch(path: Path, *, minutes_ago: int = 0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text("data", encoding="utf-8")
    timestamp = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).timestamp()
    os.utime(path, (timestamp, timestamp))


def test_write_status_json_shape(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    current_file = tmp_path / "media" / "clip.mkv"

    played_file = tmp_path / "media" / "played.mp4"
    pending_file = tmp_path / "media" / "pending.mp4"
    status_path = write_status(
        config,
        "healthy",
        "streaming",
        current_file=current_file,
        played_files=[played_file],
        pending_files=[current_file, pending_file],
        updated_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
    )

    payload = cast(dict[str, object], json.loads(status_path.read_text(encoding="utf-8")))
    assert payload == {
        "state": "healthy",
        "message": "streaming",
        "updated_at": "2026-01-02T03:04:05+00:00",
        "current_file": str(current_file),
        "played_files": [str(played_file)],
        "pending_files": [str(current_file), str(pending_file)],
    }


def test_fresh_playlist_health_success(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    playlist = config.hls_playlist_path
    touch(playlist)
    touch(config.hls_dir / "seg-0001.ts")
    _ = write_status(config, "healthy", "streaming")

    assert healthcheck(config)
    assert evaluate_health(config) == (True, "healthy")


def test_stale_playlist_health_failure(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    touch(config.hls_playlist_path, minutes_ago=2)
    touch(config.hls_dir / "seg-0001.ts", minutes_ago=2)
    _ = write_status(config, "healthy", "streaming")

    healthy, message = evaluate_health(config, max_age_seconds=30)

    assert not healthy
    assert "stale" in message
    assert not healthcheck(config, max_age_seconds=30)


def test_missing_playlist_health_failure(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    touch(config.hls_dir / "seg-0001.ts")

    assert evaluate_health(config) == (False, "playlist missing")
    assert not healthcheck(config)


@pytest.mark.parametrize(
    ("state", "message"),
    [("no_media", "waiting for media"), ("ffmpeg_failed", "ffmpeg crashed")],
)
def test_unhealthy_status_states_fail_healthcheck(
    tmp_path: Path,
    state: str,
    message: str,
) -> None:
    config = make_config(tmp_path)
    touch(config.hls_playlist_path)
    touch(config.hls_dir / "seg-0001.ts")
    _ = write_status(config, state, message)

    assert not healthcheck(config)
    assert evaluate_health(config)[0] is False


def test_cli_check_returns_non_zero_for_stale_playlist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = make_config(tmp_path)
    touch(config.hls_playlist_path, minutes_ago=2)
    touch(config.hls_dir / "seg-0001.ts", minutes_ago=2)
    monkeypatch.setattr(health_module, "load_config", lambda: config)

    assert main(["--check", "--max-age-seconds", "30"]) == 1


def test_status_file_is_readable(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    _ = write_status(config, "starting", "booting")

    payload = read_status(config)

    assert payload is not None
    assert payload["state"] == "starting"
