from __future__ import annotations

import signal
import subprocess
import time
from dataclasses import dataclass
from collections.abc import Callable, Sequence
from importlib import import_module
from pathlib import Path
from types import FrameType
from typing import Protocol, cast

from .config import ProducerConfig, load_config
from .control import skip_current_marker_path


BACKOFF_SECONDS = 5
STOP_TIMEOUT_SECONDS = 5
FFMPEG_POLL_SECONDS = 1

SleepFunc = Callable[[float], None]
PopenFactory = Callable[[Sequence[str]], subprocess.Popen[bytes]]


@dataclass(frozen=True, slots=True)
class FfmpegRunResult:
    return_code: int
    skipped: bool = False


class PlaybackQueueLike(Protocol):
    files: tuple[Path, ...]

    @property
    def has_media(self) -> bool:
        ...


class FfmpegModule(Protocol):
    def build_ffmpeg_command(self, input_path: Path, config: ProducerConfig) -> list[str]:
        ...


class HealthModule(Protocol):
    def write_status(
        self,
        config: ProducerConfig,
        state: str,
        message: str,
        *,
        current_file: Path | str | None = None,
        played_files: Sequence[Path | str] | None = None,
        pending_files: Sequence[Path | str] | None = None,
    ) -> Path:
        ...


class QueueModule(Protocol):
    def generate_playback_queue(self, config: ProducerConfig) -> PlaybackQueueLike:
        ...


ffmpeg_module = cast(FfmpegModule, cast(object, import_module(".ffmpeg", package=__package__)))
health_module = cast(HealthModule, cast(object, import_module(".health", package=__package__)))
queue_module = cast(QueueModule, cast(object, import_module(".queue", package=__package__)))
build_ffmpeg_command = ffmpeg_module.build_ffmpeg_command
generate_playback_queue = queue_module.generate_playback_queue
write_status = health_module.write_status


class Supervisor:
    def __init__(
        self,
        config: ProducerConfig,
        *,
        sleep_func: SleepFunc = time.sleep,
        popen_factory: PopenFactory = subprocess.Popen,
    ) -> None:
        self.config: ProducerConfig = config
        self._sleep: SleepFunc = sleep_func
        self._popen: PopenFactory = popen_factory
        self._stop_requested: bool = False
        self._child: subprocess.Popen[bytes] | None = None

    @property
    def stop_requested(self) -> bool:
        return self._stop_requested

    def request_stop(self, signum: int | None = None, frame: FrameType | None = None) -> None:
        _ = signum, frame
        self._stop_requested = True
        self._terminate_child()

    def run(self, *, max_cycles: int | None = None) -> int:
        self.config.hls_dir.mkdir(parents=True, exist_ok=True)
        self._cleanup_hls_outputs()
        _ = write_status(self.config, "starting", "supervisor starting")

        completed_cycles = 0
        while not self._stop_requested and (max_cycles is None or completed_cycles < max_cycles):
            queue = generate_playback_queue(self.config)
            if not queue.has_media:
                _ = write_status(self.config, "no_media", "no media found; waiting before rescan")
                self._sleep(self.config.rescan_seconds)
                completed_cycles += 1
                continue

            self._run_queue_cycle(queue, completed_cycles + 1)
            completed_cycles += 1

        return 0

    def _run_queue_cycle(self, queue: PlaybackQueueLike, cycle_number: int) -> None:
        played_files: list[Path] = []
        for index, media_path in enumerate(queue.files):
            if self._stop_requested:
                return
            pending_files = list(queue.files[index:])
            remaining_files = list(queue.files[index + 1 :])
            if not media_path.exists():
                played_files.append(media_path)
                _ = self._write_status(
                    "skipped",
                    f"media file missing; skipping cycle {cycle_number}",
                    current_file=media_path,
                    played_files=played_files,
                    pending_files=remaining_files,
                )
                continue
            if not self._stream_file(media_path, cycle_number, played_files=played_files, pending_files=pending_files):
                self._sleep(BACKOFF_SECONDS)
            if not self._stop_requested:
                played_files.append(media_path)

    def _stream_file(
        self,
        media_path: Path,
        cycle_number: int,
        *,
        played_files: Sequence[Path],
        pending_files: Sequence[Path],
    ) -> bool:
        _ = self._write_status(
            "streaming",
            f"streaming cycle {cycle_number}",
            current_file=media_path,
            played_files=played_files,
            pending_files=pending_files,
        )
        completed_files = [*played_files, media_path]
        remaining_files = list(pending_files[1:])
        try:
            command = build_ffmpeg_command(media_path, self.config)
        except Exception as error:
            _ = self._write_status(
                "ffmpeg_failed",
                f"failed to build ffmpeg command: {error}",
                current_file=media_path,
                played_files=completed_files,
                pending_files=remaining_files,
            )
            return False

        result = self._run_ffmpeg(command)
        if self._stop_requested:
            return True
        if result.skipped:
            _ = self._write_status(
                "skipped",
                "operator requested skip-current",
                current_file=media_path,
                played_files=completed_files,
                pending_files=remaining_files,
            )
            return True
        if result.return_code != 0:
            _ = self._write_status(
                "ffmpeg_failed",
                f"ffmpeg exited with code {result.return_code}",
                current_file=media_path,
                played_files=completed_files,
                pending_files=remaining_files,
            )
            return False

        _ = self._write_status(
            "streaming",
            f"completed cycle {cycle_number} file",
            current_file=media_path,
            played_files=completed_files,
            pending_files=remaining_files,
        )
        return True

    def _run_ffmpeg(self, command: list[str]) -> FfmpegRunResult:
        _ = self._consume_skip_request()
        process = self._popen(command)
        self._child = process
        try:
            while True:
                return_code = process.poll()
                if return_code is not None:
                    return FfmpegRunResult(return_code=return_code)
                if self._consume_skip_request():
                    self._terminate_process(process)
                    return FfmpegRunResult(return_code=0, skipped=True)
                self._sleep(FFMPEG_POLL_SECONDS)
        finally:
            if self._child is process:
                self._child = None

    def _consume_skip_request(self) -> bool:
        marker_path = skip_current_marker_path(self.config)
        try:
            marker_path.unlink()
        except FileNotFoundError:
            return False
        return True

    def _cleanup_hls_outputs(self) -> None:
        for candidate in self.config.hls_dir.iterdir():
            if not candidate.is_file():
                continue
            if candidate == self.config.hls_playlist_path or _is_producer_segment(candidate):
                candidate.unlink()

    def _write_status(
        self,
        state: str,
        message: str,
        *,
        current_file: Path | str | None = None,
        played_files: Sequence[Path | str] | None = None,
        pending_files: Sequence[Path | str] | None = None,
    ) -> Path:
        return write_status(
            self.config,
            state,
            message,
            current_file=current_file,
            played_files=played_files,
            pending_files=pending_files,
        )

    def _terminate_child(self) -> None:
        process = self._child
        if process is None:
            return
        self._terminate_process(process)

    def _terminate_process(self, process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            _ = process.wait(timeout=STOP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            _ = process.wait(timeout=STOP_TIMEOUT_SECONDS)


def _is_producer_segment(path: Path) -> bool:
    name = path.name
    prefix = "live-"
    suffix = ".ts"
    if not name.startswith(prefix) or not name.endswith(suffix):
        return False
    segment_number = name[len(prefix) : -len(suffix)]
    return len(segment_number) == 9 and segment_number.isdigit()


def install_signal_handlers(supervisor: Supervisor) -> None:
    _ = signal.signal(signal.SIGTERM, supervisor.request_stop)
    _ = signal.signal(signal.SIGINT, supervisor.request_stop)


def run_supervisor(
    config: ProducerConfig | None = None,
    *,
    max_cycles: int | None = None,
    sleep_func: SleepFunc = time.sleep,
    popen_factory: PopenFactory = subprocess.Popen,
    install_handlers: bool = True,
) -> int:
    supervisor = Supervisor(
        load_config() if config is None else config,
        sleep_func=sleep_func,
        popen_factory=popen_factory,
    )
    if install_handlers:
        install_signal_handlers(supervisor)
    return supervisor.run(max_cycles=max_cycles)


def main() -> int:
    return run_supervisor()


if __name__ == "__main__":
    raise SystemExit(main())
