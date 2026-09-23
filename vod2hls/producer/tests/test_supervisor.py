from __future__ import annotations

import signal
import subprocess
from collections.abc import Callable, Sequence
from importlib import import_module
from pathlib import Path
from types import FrameType
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


class ProducerConfigFactory(Protocol):
    def __call__(
        self,
        media_dir: Path = ...,
        hls_dir: Path = ...,
        hls_playlist: str = "live.m3u8",
        segment_seconds: int = 4,
        playlist_size: int = 6,
        rescan_seconds: int = 30,
        output_width: int = 1920,
        output_height: int = 1080,
        output_fps: int = 30,
        supported_extensions: tuple[str, ...] = ...,
        shuffle_seed: int | None = None,
        log_level: str = "INFO",
    ) -> ProducerConfigLike:
        ...


class ConfigModuleLike(Protocol):
    ProducerConfig: ProducerConfigFactory


class PlaybackQueueLike(Protocol):
    files: tuple[Path, ...]

    @property
    def has_media(self) -> bool:
        ...


class PlaybackQueueFactory(Protocol):
    def __call__(self, *, files: tuple[Path, ...]) -> PlaybackQueueLike:
        ...


class QueueModuleLike(Protocol):
    PlaybackQueue: PlaybackQueueFactory


class ControlModuleLike(Protocol):
    SKIP_CURRENT_REQUEST_FILENAME: str


class SupervisorLike(Protocol):
    @property
    def stop_requested(self) -> bool:
        ...

    def request_stop(self, signum: int | None = None, frame: FrameType | None = None) -> None:
        ...

    def run(self, *, max_cycles: int | None = None) -> int:
        ...


SleepFunc = Callable[[float], None]
PopenFactory = Callable[[Sequence[str]], object]


class SupervisorFactory(Protocol):
    def __call__(
        self,
        config: ProducerConfigLike,
        *,
        sleep_func: SleepFunc = ...,
        popen_factory: PopenFactory = ...,
    ) -> SupervisorLike:
        ...


class SupervisorModuleLike(Protocol):
    BACKOFF_SECONDS: int
    Supervisor: SupervisorFactory

    def run_supervisor(
        self,
        config: ProducerConfigLike | None = None,
        *,
        max_cycles: int | None = None,
        sleep_func: SleepFunc = ...,
        popen_factory: PopenFactory = ...,
        install_handlers: bool = True,
    ) -> int:
        ...


config_module = cast(ConfigModuleLike, cast(object, import_module("app.config")))
control_module = cast(ControlModuleLike, cast(object, import_module("app.control")))
queue_module = cast(QueueModuleLike, cast(object, import_module("app.queue")))
supervisor_module = cast(SupervisorModuleLike, cast(object, import_module("app.supervisor")))
ProducerConfig = config_module.ProducerConfig
PlaybackQueue = queue_module.PlaybackQueue
Supervisor = supervisor_module.Supervisor
run_supervisor = supervisor_module.run_supervisor


def make_config(tmp_path: Path, *, rescan_seconds: int = 7) -> ProducerConfigLike:
    return ProducerConfig(media_dir=tmp_path / "media", hls_dir=tmp_path / "hls", rescan_seconds=rescan_seconds)


def touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_bytes(b"media")
    return path


def test_startup_removes_stale_hls_outputs_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = make_config(tmp_path)
    stale_playlist = config.hls_playlist_path
    stale_segment = config.hls_dir / "live-000000001.ts"
    stale_audio = config.hls_dir / "audio.aac"
    unrelated_segment = config.hls_dir / "archive.ts"
    partial_segment_name = config.hls_dir / "live-1.ts"
    other_playlist = config.hls_dir / "other.m3u8"
    unrelated_fragment = config.hls_dir / "clip.m4s"
    unrelated_audio = config.hls_dir / "music.m4a"
    status_file = config.hls_dir / "status.json"
    skip_marker = config.hls_dir / control_module.SKIP_CURRENT_REQUEST_FILENAME
    unrelated_file = config.hls_dir / "notes.txt"
    subdirectory = config.hls_dir / "nested"

    for file_path in (
        stale_playlist,
        stale_segment,
        stale_audio,
        unrelated_segment,
        partial_segment_name,
        other_playlist,
        unrelated_fragment,
        unrelated_audio,
        status_file,
        skip_marker,
        unrelated_file,
    ):
        file_path.parent.mkdir(parents=True, exist_ok=True)
        _ = file_path.write_text("stale", encoding="utf-8")
    subdirectory.mkdir(parents=True)

    def fake_queue(config_arg: ProducerConfigLike) -> PlaybackQueueLike:
        _ = config_arg
        return PlaybackQueue(files=())

    monkeypatch.setattr("app.supervisor.generate_playback_queue", fake_queue)

    exit_code = run_supervisor(config, max_cycles=1, sleep_func=lambda _: None, install_handlers=False)

    assert exit_code == 0
    assert not stale_playlist.exists()
    assert not stale_segment.exists()
    assert stale_audio.exists()
    assert unrelated_segment.exists()
    assert partial_segment_name.exists()
    assert other_playlist.exists()
    assert unrelated_fragment.exists()
    assert unrelated_audio.exists()
    assert status_file.exists()
    assert skip_marker.exists()
    assert unrelated_file.exists()
    assert subdirectory.is_dir()


def test_no_media_writes_status_sleeps_and_rescans(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = make_config(tmp_path, rescan_seconds=9)
    statuses: list[tuple[str, Path | str | None, list[str] | None, list[str] | None]] = []
    sleeps: list[float] = []
    scans: list[ProducerConfigLike] = []

    def fake_queue(config_arg: ProducerConfigLike) -> PlaybackQueueLike:
        scans.append(config_arg)
        return PlaybackQueue(files=())

    def fake_status(
        config_arg: ProducerConfigLike,
        state: str,
        message: str,
        *,
        current_file: Path | str | None = None,
        played_files: Sequence[Path | str] | None = None,
        pending_files: Sequence[Path | str] | None = None,
    ) -> Path:
        _ = message
        statuses.append((
            state,
            current_file,
            None if played_files is None else [str(file_path) for file_path in played_files],
            None if pending_files is None else [str(file_path) for file_path in pending_files],
        ))
        return config_arg.hls_dir / "status.json"

    monkeypatch.setattr("app.supervisor.generate_playback_queue", fake_queue)
    monkeypatch.setattr("app.supervisor.write_status", fake_status)

    exit_code = run_supervisor(config, max_cycles=1, sleep_func=sleeps.append, install_handlers=False)

    assert exit_code == 0
    assert config.hls_dir.is_dir()
    assert scans == [config]
    assert sleeps == [9]
    assert statuses[0][0] == "starting"
    assert statuses[-1][0] == "no_media"


def test_supervisor_runs_shuffled_queue_cycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = make_config(tmp_path)
    first_file = touch(config.media_dir / "first.mp4")
    second_file = touch(config.media_dir / "second.mkv")
    queued_files = (second_file, first_file)
    statuses: list[tuple[str, Path | str | None, list[str] | None, list[str] | None]] = []
    commands: list[list[str]] = []

    class Process:
        def wait(self, timeout: float | None = None) -> int:
            _ = timeout
            return 0

        def poll(self) -> int | None:
            return 0

        def terminate(self) -> None:
            raise AssertionError("completed process should not be terminated")

        def kill(self) -> None:
            raise AssertionError("completed process should not be killed")

    def fake_status(
        config_arg: ProducerConfigLike,
        state: str,
        message: str,
        *,
        current_file: Path | str | None = None,
        played_files: Sequence[Path | str] | None = None,
        pending_files: Sequence[Path | str] | None = None,
    ) -> Path:
        _ = message
        statuses.append((
            state,
            current_file,
            None if played_files is None else [str(file_path) for file_path in played_files],
            None if pending_files is None else [str(file_path) for file_path in pending_files],
        ))
        return config_arg.hls_dir / "status.json"

    def fake_build(input_path: Path, config_arg: ProducerConfigLike) -> list[str]:
        assert config_arg is config
        return ["ffmpeg", "-i", str(input_path)]

    def fake_popen(command: Sequence[str]) -> Process:
        commands.append(list(command))
        return Process()

    def fake_queue(config_arg: ProducerConfigLike) -> PlaybackQueueLike:
        _ = config_arg
        return PlaybackQueue(files=queued_files)

    monkeypatch.setattr("app.supervisor.generate_playback_queue", fake_queue)
    monkeypatch.setattr("app.supervisor.write_status", fake_status)
    monkeypatch.setattr("app.supervisor.build_ffmpeg_command", fake_build)

    exit_code = run_supervisor(config, max_cycles=1, popen_factory=fake_popen, install_handlers=False)

    assert exit_code == 0
    assert commands == [["ffmpeg", "-i", str(second_file)], ["ffmpeg", "-i", str(first_file)]]
    streaming_files = [current_file for state, current_file, _, _ in statuses if state == "streaming"]
    assert second_file in streaming_files
    assert first_file in streaming_files
    assert (
        "streaming",
        second_file,
        [],
        [str(second_file), str(first_file)],
    ) in statuses
    assert (
        "streaming",
        second_file,
        [str(second_file)],
        [str(first_file)],
    ) in statuses
    assert (
        "streaming",
        first_file,
        [str(second_file)],
        [str(first_file)],
    ) in statuses
    assert (
        "streaming",
        first_file,
        [str(second_file), str(first_file)],
        [],
    ) in statuses


def test_skip_current_marker_terminates_current_child_and_runs_next(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(tmp_path)
    first_file = touch(config.media_dir / "first.mp4")
    next_file = touch(config.media_dir / "next.mp4")
    marker_path = config.hls_dir / control_module.SKIP_CURRENT_REQUEST_FILENAME
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    statuses: list[tuple[str, str, Path | str | None, list[str] | None, list[str] | None]] = []
    sleeps: list[float] = []
    commands: list[list[str]] = []
    events: list[str] = []

    class Process:
        def __init__(self, command: Sequence[str]) -> None:
            self.command: list[str] = list(command)
            self.running: bool = str(first_file) in self.command
            self.terminated: bool = False

        def wait(self, timeout: float | None = None) -> int:
            _ = timeout
            self.running = False
            return -15 if self.terminated else 0

        def poll(self) -> int | None:
            if self.running:
                return None
            return -15 if self.terminated else 0

        def terminate(self) -> None:
            events.append(f"terminate:{self.command[-1]}")
            self.terminated = True

        def kill(self) -> None:
            events.append(f"kill:{self.command[-1]}")

    def fake_status(
        config_arg: ProducerConfigLike,
        state: str,
        message: str,
        *,
        current_file: Path | str | None = None,
        played_files: Sequence[Path | str] | None = None,
        pending_files: Sequence[Path | str] | None = None,
    ) -> Path:
        statuses.append((
            state,
            message,
            current_file,
            None if played_files is None else [str(file_path) for file_path in played_files],
            None if pending_files is None else [str(file_path) for file_path in pending_files],
        ))
        return config_arg.hls_dir / "status.json"

    def fake_queue(config_arg: ProducerConfigLike) -> PlaybackQueueLike:
        _ = config_arg
        return PlaybackQueue(files=(first_file, next_file))

    def fake_popen(command: Sequence[str]) -> Process:
        commands.append(list(command))
        return Process(command)

    def request_skip_after_poll(seconds: float) -> None:
        sleeps.append(seconds)
        _ = marker_path.write_text("skip-current\n", encoding="utf-8")

    monkeypatch.setattr("app.supervisor.generate_playback_queue", fake_queue)
    monkeypatch.setattr("app.supervisor.write_status", fake_status)
    monkeypatch.setattr("app.supervisor.build_ffmpeg_command", fake_build_command)

    exit_code = run_supervisor(
        config,
        max_cycles=1,
        sleep_func=request_skip_after_poll,
        popen_factory=fake_popen,
        install_handlers=False,
    )

    assert exit_code == 0
    assert commands == [["ffmpeg", str(first_file)], ["ffmpeg", str(next_file)]]
    assert events == [f"terminate:{first_file}"]
    assert sleeps == [1]
    assert not marker_path.exists()
    assert (
        "skipped",
        "operator requested skip-current",
        first_file,
        [str(first_file)],
        [str(next_file)],
    ) in statuses
    assert all(status[:3] != ("ffmpeg_failed", "ffmpeg exited with code -15", first_file) for status in statuses)
    assert (
        "streaming",
        "completed cycle 1 file",
        next_file,
        [str(first_file), str(next_file)],
        [],
    ) in statuses


def test_stale_skip_marker_before_ffmpeg_start_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(tmp_path)
    first_file = touch(config.media_dir / "first.mp4")
    next_file = touch(config.media_dir / "next.mp4")
    marker_path = config.hls_dir / control_module.SKIP_CURRENT_REQUEST_FILENAME
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    _ = marker_path.write_text("skip-current\n", encoding="utf-8")
    statuses: list[tuple[str, str, Path | str | None, list[str] | None, list[str] | None]] = []
    commands: list[list[str]] = []
    events: list[str] = []

    class Process:
        def __init__(self, command: Sequence[str]) -> None:
            self.command: list[str] = list(command)

        def wait(self, timeout: float | None = None) -> int:
            _ = timeout
            return 0

        def poll(self) -> int:
            return 0

        def terminate(self) -> None:
            events.append(f"terminate:{self.command[-1]}")

        def kill(self) -> None:
            events.append(f"kill:{self.command[-1]}")

    def fake_status(
        config_arg: ProducerConfigLike,
        state: str,
        message: str,
        *,
        current_file: Path | str | None = None,
        played_files: Sequence[Path | str] | None = None,
        pending_files: Sequence[Path | str] | None = None,
    ) -> Path:
        statuses.append((
            state,
            message,
            current_file,
            None if played_files is None else [str(file_path) for file_path in played_files],
            None if pending_files is None else [str(file_path) for file_path in pending_files],
        ))
        return config_arg.hls_dir / "status.json"

    def fake_queue(config_arg: ProducerConfigLike) -> PlaybackQueueLike:
        _ = config_arg
        return PlaybackQueue(files=(first_file, next_file))

    def fake_popen(command: Sequence[str]) -> Process:
        commands.append(list(command))
        return Process(command)

    monkeypatch.setattr("app.supervisor.generate_playback_queue", fake_queue)
    monkeypatch.setattr("app.supervisor.write_status", fake_status)
    monkeypatch.setattr("app.supervisor.build_ffmpeg_command", fake_build_command)

    exit_code = run_supervisor(config, max_cycles=1, popen_factory=fake_popen, install_handlers=False)

    assert exit_code == 0
    assert commands == [["ffmpeg", str(first_file)], ["ffmpeg", str(next_file)]]
    assert events == []
    assert not marker_path.exists()
    assert all(status[:3] != ("skipped", "operator requested skip-current", first_file) for status in statuses)
    assert (
        "streaming",
        "completed cycle 1 file",
        first_file,
        [str(first_file)],
        [str(next_file)],
    ) in statuses
    assert (
        "streaming",
        "completed cycle 1 file",
        next_file,
        [str(first_file), str(next_file)],
        [],
    ) in statuses


def test_skip_marker_created_after_child_exit_is_ignored_for_next_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(tmp_path)
    first_file = touch(config.media_dir / "first.mp4")
    next_file = touch(config.media_dir / "next.mp4")
    marker_path = config.hls_dir / control_module.SKIP_CURRENT_REQUEST_FILENAME
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    statuses: list[tuple[str, str, Path | str | None]] = []
    commands: list[list[str]] = []
    events: list[str] = []

    class Process:
        def __init__(self, command: Sequence[str]) -> None:
            self.command: list[str] = list(command)

        def wait(self, timeout: float | None = None) -> int:
            _ = timeout
            return 0

        def poll(self) -> int:
            if str(first_file) in self.command:
                _ = marker_path.write_text("skip-current\n", encoding="utf-8")
            return 0

        def terminate(self) -> None:
            events.append(f"terminate:{self.command[-1]}")

        def kill(self) -> None:
            events.append(f"kill:{self.command[-1]}")

    def fake_status(config_arg: ProducerConfigLike, state: str, message: str, *, current_file: Path | str | None = None, played_files: Sequence[Path | str] | None = None, pending_files: Sequence[Path | str] | None = None) -> Path:
        _ = played_files, pending_files
        statuses.append((state, message, current_file))
        return config_arg.hls_dir / "status.json"

    def fake_queue(config_arg: ProducerConfigLike) -> PlaybackQueueLike:
        _ = config_arg
        return PlaybackQueue(files=(first_file, next_file))

    def fake_popen(command: Sequence[str]) -> Process:
        commands.append(list(command))
        return Process(command)

    monkeypatch.setattr("app.supervisor.generate_playback_queue", fake_queue)
    monkeypatch.setattr("app.supervisor.write_status", fake_status)
    monkeypatch.setattr("app.supervisor.build_ffmpeg_command", fake_build_command)

    exit_code = run_supervisor(config, max_cycles=1, popen_factory=fake_popen, install_handlers=False)

    assert exit_code == 0
    assert commands == [["ffmpeg", str(first_file)], ["ffmpeg", str(next_file)]]
    assert events == []
    assert not marker_path.exists()
    assert ("skipped", "operator requested skip-current", next_file) not in statuses
    assert ("streaming", "completed cycle 1 file", first_file) in statuses
    assert ("streaming", "completed cycle 1 file", next_file) in statuses


def test_sigterm_stops_child_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = make_config(tmp_path)
    media_file = touch(config.media_dir / "movie.mp4")
    events: list[str] = []
    supervisor: SupervisorLike

    class Process:
        def __init__(self) -> None:
            self.running: bool = True
            self.signal_sent: bool = False

        def wait(self, timeout: float | None = None) -> int:
            _ = timeout
            events.append("wait-timeout")
            self.running = False
            return -15

        def poll(self) -> int | None:
            if self.running and not self.signal_sent:
                self.signal_sent = True
                supervisor.request_stop(signal.SIGTERM, None)
            return None if self.running else -15

        def terminate(self) -> None:
            events.append("terminate")

        def kill(self) -> None:
            events.append("kill")

    def fake_queue(config_arg: ProducerConfigLike) -> PlaybackQueueLike:
        _ = config_arg
        return PlaybackQueue(files=(media_file,))

    def fake_build(input_path: Path, config_arg: ProducerConfigLike) -> list[str]:
        _ = config_arg
        return ["ffmpeg", str(input_path)]

    def fake_popen(command: Sequence[str]) -> Process:
        _ = command
        return Process()

    monkeypatch.setattr("app.supervisor.generate_playback_queue", fake_queue)
    monkeypatch.setattr("app.supervisor.build_ffmpeg_command", fake_build)
    supervisor = Supervisor(config, popen_factory=fake_popen)

    exit_code = supervisor.run(max_cycles=1)

    assert exit_code == 0
    assert supervisor.stop_requested is True
    assert events == ["terminate", "wait-timeout"]


def test_sigterm_kills_child_when_terminate_times_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = make_config(tmp_path)
    media_file = touch(config.media_dir / "movie.mp4")
    events: list[str] = []
    supervisor: SupervisorLike

    class Process:
        def __init__(self) -> None:
            self.signal_sent: bool = False

        def wait(self, timeout: float | None = None) -> int:
            if "kill" not in events:
                raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=0 if timeout is None else timeout)
            return -9

        def poll(self) -> int | None:
            if not self.signal_sent:
                self.signal_sent = True
                supervisor.request_stop(signal.SIGTERM, None)
            if "kill" in events:
                return -9
            return None

        def terminate(self) -> None:
            events.append("terminate")

        def kill(self) -> None:
            events.append("kill")

    def fake_queue(config_arg: ProducerConfigLike) -> PlaybackQueueLike:
        _ = config_arg
        return PlaybackQueue(files=(media_file,))

    def fake_build(input_path: Path, config_arg: ProducerConfigLike) -> list[str]:
        _ = config_arg
        return ["ffmpeg", str(input_path)]

    def fake_popen(command: Sequence[str]) -> Process:
        _ = command
        return Process()

    monkeypatch.setattr("app.supervisor.generate_playback_queue", fake_queue)
    monkeypatch.setattr("app.supervisor.build_ffmpeg_command", fake_build)
    supervisor = Supervisor(config, popen_factory=fake_popen)

    assert supervisor.run(max_cycles=1) == 0
    assert events == ["terminate", "kill"]


def test_ffmpeg_failure_writes_status_and_backs_off_then_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(tmp_path)
    failed_file = touch(config.media_dir / "failed.mp4")
    next_file = touch(config.media_dir / "next.mp4")
    statuses: list[tuple[str, str, Path | str | None, list[str] | None, list[str] | None]] = []
    sleeps: list[float] = []
    commands: list[list[str]] = []

    class Process:
        def __init__(self, return_code: int) -> None:
            self._return_code: int = return_code

        def wait(self, timeout: float | None = None) -> int:
            _ = timeout
            return self._return_code

        def poll(self) -> int:
            return self._return_code

        def terminate(self) -> None:
            raise AssertionError("failed process already exited")

        def kill(self) -> None:
            raise AssertionError("failed process already exited")

    def fake_status(
        config_arg: ProducerConfigLike,
        state: str,
        message: str,
        *,
        current_file: Path | str | None = None,
        played_files: Sequence[Path | str] | None = None,
        pending_files: Sequence[Path | str] | None = None,
    ) -> Path:
        statuses.append((
            state,
            message,
            current_file,
            None if played_files is None else [str(file_path) for file_path in played_files],
            None if pending_files is None else [str(file_path) for file_path in pending_files],
        ))
        return config_arg.hls_dir / "status.json"

    def fake_popen(command: Sequence[str]) -> Process:
        commands.append(list(command))
        return Process(1 if str(failed_file) in command else 0)

    def fake_queue(config_arg: ProducerConfigLike) -> PlaybackQueueLike:
        _ = config_arg
        return PlaybackQueue(files=(failed_file, next_file))

    monkeypatch.setattr("app.supervisor.generate_playback_queue", fake_queue)
    monkeypatch.setattr("app.supervisor.write_status", fake_status)
    monkeypatch.setattr("app.supervisor.build_ffmpeg_command", fake_build_command)

    exit_code = run_supervisor(
        config,
        max_cycles=1,
        sleep_func=sleeps.append,
        popen_factory=fake_popen,
        install_handlers=False,
    )

    assert exit_code == 0
    assert commands == [["ffmpeg", str(failed_file)], ["ffmpeg", str(next_file)]]
    assert sleeps == [supervisor_module.BACKOFF_SECONDS]
    assert (
        "ffmpeg_failed",
        "ffmpeg exited with code 1",
        failed_file,
        [str(failed_file)],
        [str(next_file)],
    ) in statuses


def test_missing_file_is_skipped_without_running_ffmpeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = make_config(tmp_path)
    missing_file = config.media_dir / "missing.mp4"
    present_file = touch(config.media_dir / "present.mp4")
    statuses: list[tuple[str, Path | str | None, list[str] | None, list[str] | None]] = []
    commands: list[list[str]] = []

    def fake_status(
        config_arg: ProducerConfigLike,
        state: str,
        message: str,
        *,
        current_file: Path | str | None = None,
        played_files: Sequence[Path | str] | None = None,
        pending_files: Sequence[Path | str] | None = None,
    ) -> Path:
        _ = message
        statuses.append((
            state,
            current_file,
            None if played_files is None else [str(file_path) for file_path in played_files],
            None if pending_files is None else [str(file_path) for file_path in pending_files],
        ))
        return config_arg.hls_dir / "status.json"

    def fake_queue(config_arg: ProducerConfigLike) -> PlaybackQueueLike:
        _ = config_arg
        return PlaybackQueue(files=(missing_file, present_file))

    def fake_popen(command: Sequence[str]) -> SuccessfulProcess:
        commands.append(list(command))
        return SuccessfulProcess()

    monkeypatch.setattr("app.supervisor.generate_playback_queue", fake_queue)
    monkeypatch.setattr("app.supervisor.write_status", fake_status)
    monkeypatch.setattr("app.supervisor.build_ffmpeg_command", fake_build_command)
    exit_code = run_supervisor(
        config,
        max_cycles=1,
        popen_factory=fake_popen,
        install_handlers=False,
    )

    assert exit_code == 0
    assert ("skipped", missing_file, [str(missing_file)], [str(present_file)]) in statuses
    assert commands == [["ffmpeg", str(present_file)]]


def test_corrupt_file_command_build_failure_writes_status_and_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(tmp_path)
    corrupt_file = touch(config.media_dir / "corrupt.mp4")
    next_file = touch(config.media_dir / "next.mp4")
    statuses: list[tuple[str, Path | str | None, list[str] | None, list[str] | None]] = []
    sleeps: list[float] = []
    commands: list[list[str]] = []

    def fake_build(input_path: Path, config_arg: ProducerConfigLike) -> list[str]:
        _ = config_arg
        if input_path == corrupt_file:
            raise ValueError("unprobeable input")
        return ["ffmpeg", str(input_path)]

    def fake_status(
        config_arg: ProducerConfigLike,
        state: str,
        message: str,
        *,
        current_file: Path | str | None = None,
        played_files: Sequence[Path | str] | None = None,
        pending_files: Sequence[Path | str] | None = None,
    ) -> Path:
        _ = message
        statuses.append((
            state,
            current_file,
            None if played_files is None else [str(file_path) for file_path in played_files],
            None if pending_files is None else [str(file_path) for file_path in pending_files],
        ))
        return config_arg.hls_dir / "status.json"

    def fake_queue(config_arg: ProducerConfigLike) -> PlaybackQueueLike:
        _ = config_arg
        return PlaybackQueue(files=(corrupt_file, next_file))

    def fake_popen(command: Sequence[str]) -> SuccessfulProcess:
        commands.append(list(command))
        return SuccessfulProcess()

    monkeypatch.setattr("app.supervisor.generate_playback_queue", fake_queue)
    monkeypatch.setattr("app.supervisor.write_status", fake_status)
    monkeypatch.setattr("app.supervisor.build_ffmpeg_command", fake_build)

    assert run_supervisor(
        config,
        max_cycles=1,
        sleep_func=sleeps.append,
        popen_factory=fake_popen,
        install_handlers=False,
    ) == 0
    assert ("ffmpeg_failed", corrupt_file, [str(corrupt_file)], [str(next_file)]) in statuses
    assert sleeps == [supervisor_module.BACKOFF_SECONDS]
    assert commands == [["ffmpeg", str(next_file)]]


class SuccessfulProcess:
    def wait(self, timeout: float | None = None) -> int:
        _ = timeout
        return 0

    def poll(self) -> int:
        return 0

    def terminate(self) -> None:
        raise AssertionError("successful process should not be terminated")

    def kill(self) -> None:
        raise AssertionError("successful process should not be killed")


def fake_build_command(input_path: Path, config_arg: ProducerConfigLike) -> list[str]:
    _ = config_arg
    return ["ffmpeg", str(input_path)]
