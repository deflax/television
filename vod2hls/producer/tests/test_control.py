from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import Protocol, cast

import pytest


class ProducerConfigLike(Protocol):
    hls_dir: Path


class ProducerConfigFactory(Protocol):
    def __call__(self, *, hls_dir: Path) -> ProducerConfigLike:
        ...


class ConfigModuleLike(Protocol):
    ProducerConfig: ProducerConfigFactory


class ControlModuleLike(Protocol):
    SKIP_CURRENT_REQUEST_FILENAME: str

    def load_config(self) -> ProducerConfigLike:
        ...

    def skip_current_marker_path(self, config: ProducerConfigLike) -> Path:
        ...

    def request_skip_current(self, config: ProducerConfigLike) -> Path:
        ...

    def main(self, argv: list[str] | None = None) -> int:
        ...


config_module = cast(ConfigModuleLike, cast(object, import_module("app.config")))
control_module = cast(ControlModuleLike, cast(object, import_module("app.control")))
ProducerConfig = config_module.ProducerConfig
skip_current_marker_path = control_module.skip_current_marker_path
request_skip_current = control_module.request_skip_current
main = control_module.main


def make_config(tmp_path: Path) -> ProducerConfigLike:
    return ProducerConfig(hls_dir=tmp_path / "hls")


def test_request_skip_current_creates_marker_in_hls_dir(tmp_path: Path) -> None:
    config = make_config(tmp_path)

    marker_path = request_skip_current(config)

    assert marker_path == config.hls_dir / control_module.SKIP_CURRENT_REQUEST_FILENAME
    assert skip_current_marker_path(config) == marker_path
    assert marker_path.read_text(encoding="utf-8") == "skip-current\n"
    assert list(config.hls_dir.glob("*.tmp")) == []


def test_cli_skip_current_writes_marker_and_reports_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = make_config(tmp_path)
    monkeypatch.setattr(control_module, "load_config", lambda: config)

    exit_code = main(["skip-current"])

    marker_path = skip_current_marker_path(config)
    assert exit_code == 0
    assert marker_path.exists()
    assert str(marker_path) in capsys.readouterr().out
