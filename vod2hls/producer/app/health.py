from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Sequence
from typing import Protocol, cast

from .config import ProducerConfig, load_config


DEFAULT_HEALTH_MAX_AGE_SECONDS = 15
UNHEALTHY_STATES = {"no_media", "ffmpeg_failed", "stale"}
RELEVANT_HLS_SUFFIXES = {".m3u8", ".ts", ".m4s", ".m4a", ".aac"}


class _HealthArgs(Protocol):
    check: bool
    max_age_seconds: int


def write_status(
    config: ProducerConfig,
    state: str,
    message: str,
    *,
    current_file: Path | str | None = None,
    played_files: Sequence[Path | str] | None = None,
    pending_files: Sequence[Path | str] | None = None,
    updated_at: datetime | None = None,
) -> Path:
    status_path = config.hls_dir / "status.json"
    status_path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "state": state,
        "message": message,
        "updated_at": _format_timestamp(updated_at),
    }
    if current_file is not None:
        payload["current_file"] = str(current_file)
    if played_files is not None:
        payload["played_files"] = [str(file_path) for file_path in played_files]
    if pending_files is not None:
        payload["pending_files"] = [str(file_path) for file_path in pending_files]
    _ = status_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return status_path


def read_status(config: ProducerConfig) -> dict[str, object] | None:
    status_path = config.hls_dir / "status.json"
    if not status_path.exists():
        return None
    return cast(dict[str, object], json.loads(status_path.read_text(encoding="utf-8")))


def evaluate_health(
    config: ProducerConfig,
    *,
    max_age_seconds: int = DEFAULT_HEALTH_MAX_AGE_SECONDS,
) -> tuple[bool, str]:
    status = read_status(config)
    if status is not None and status.get("state") in UNHEALTHY_STATES:
        message = status.get("message") or status["state"]
        return False, str(message)

    playlist_path = config.hls_playlist_path
    if not playlist_path.exists():
        return False, "playlist missing"

    newest_mtime = _newest_hls_mtime(config)
    if newest_mtime is None:
        return False, "no HLS outputs found"

    age_seconds = (datetime.now(timezone.utc) - datetime.fromtimestamp(newest_mtime, tz=timezone.utc)).total_seconds()
    if age_seconds > max_age_seconds:
        return False, f"HLS output stale by {age_seconds:.1f}s"

    return True, "healthy"


def healthcheck(config: ProducerConfig, *, max_age_seconds: int = DEFAULT_HEALTH_MAX_AGE_SECONDS) -> bool:
    return evaluate_health(config, max_age_seconds=max_age_seconds)[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Producer status and health utilities")
    _ = parser.add_argument("--check", action="store_true", help="Exit 0 only when the HLS output is healthy")
    _ = parser.add_argument("--max-age-seconds", type=int, default=DEFAULT_HEALTH_MAX_AGE_SECONDS)
    args = cast(_HealthArgs, cast(object, parser.parse_args(argv)))

    config = load_config()
    healthy, message = evaluate_health(config, max_age_seconds=args.max_age_seconds)
    if args.check:
        if healthy:
            return 0
        print(message)
        return 1
    print(message)
    return 0 if healthy else 1


def _newest_hls_mtime(config: ProducerConfig) -> float | None:
    mtimes: list[float] = []
    playlist_path = config.hls_playlist_path
    if playlist_path.exists():
        mtimes.append(playlist_path.stat().st_mtime)

    if config.hls_dir.exists():
        for candidate in config.hls_dir.iterdir():
            if not candidate.is_file() or candidate.name == "status.json":
                continue
            if candidate == playlist_path or candidate.suffix.lower() in RELEVANT_HLS_SUFFIXES:
                mtimes.append(candidate.stat().st_mtime)

    if not mtimes:
        return None
    return max(mtimes)


def _format_timestamp(updated_at: datetime | None) -> str:
    if updated_at is None:
        updated_at = datetime.now(timezone.utc)
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    return updated_at.astimezone(timezone.utc).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
