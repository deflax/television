from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path
from typing import Protocol, cast

from .config import ProducerConfig, load_config


SKIP_CURRENT_REQUEST_FILENAME = "skip-current.request"


class _ControlArgs(Protocol):
    command: str


def skip_current_marker_path(config: ProducerConfig) -> Path:
    return config.hls_dir / SKIP_CURRENT_REQUEST_FILENAME


def request_skip_current(config: ProducerConfig) -> Path:
    config.hls_dir.mkdir(parents=True, exist_ok=True)
    marker_path = skip_current_marker_path(config)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=config.hls_dir,
            prefix=f".{SKIP_CURRENT_REQUEST_FILENAME}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            _ = temp_file.write("skip-current\n")
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, marker_path)
    except Exception:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise
    return marker_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Producer operator control commands")
    subparsers = parser.add_subparsers(dest="command", required=True)
    _ = subparsers.add_parser("skip-current", help="Skip the currently running media file")
    args = cast(_ControlArgs, cast(object, parser.parse_args(argv)))

    config = load_config()
    if args.command == "skip-current":
        marker_path = request_skip_current(config)
        print(f"skip-current requested: {marker_path}")
        return 0

    parser.error(f"unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
