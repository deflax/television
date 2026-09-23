from pathlib import Path
from importlib import import_module
from collections.abc import Callable
from typing import Protocol, cast

import pytest


AudioProbe = Callable[[Path], bool]


class ProducerConfigLike(Protocol):
    pass


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


class FfmpegModule(Protocol):
    SILENT_AUDIO_SOURCE: str
    ProducerConfig: ProducerConfigFactory

    def build_ffmpeg_command(
        self,
        input_path: Path,
        config: ProducerConfigLike,
        audio_probe: AudioProbe,
    ) -> list[str]:
        ...

    def input_has_audio(self, input_path: Path) -> bool:
        ...


ffmpeg_module = cast(FfmpegModule, cast(object, import_module("app.ffmpeg")))
SILENT_AUDIO_SOURCE = ffmpeg_module.SILENT_AUDIO_SOURCE
ProducerConfig = ffmpeg_module.ProducerConfig
build_ffmpeg_command = ffmpeg_module.build_ffmpeg_command
input_has_audio = ffmpeg_module.input_has_audio


def test_command_is_argument_list_not_shell() -> None:
    command = build_ffmpeg_command(Path('/media/weird "name".mp4'), ProducerConfig(), lambda _: True)

    assert isinstance(command, list)
    assert all(isinstance(argument, str) for argument in command)
    assert command[0] == "ffmpeg"
    assert str(Path('/media/weird "name".mp4')) in command
    assert not any("shell" + "=True" in argument for argument in command)


def test_command_reads_input_at_realtime_speed() -> None:
    command = build_ffmpeg_command(Path("/media/input.mp4"), ProducerConfig(), lambda _: True)

    assert "-re" in command
    assert command.index("-re") < _option_index(command, "-i")


def test_live_hls_command_flags() -> None:
    config = ProducerConfig()

    command = build_ffmpeg_command(Path("/media/input.mp4"), config, lambda _: True)

    assert command.count("-i") == 1
    assert command[_option_index(command, "-i") + 1] == "/media/input.mp4"
    assert _option_value(command, "-c:v") == "libx264"
    assert _option_value(command, "-b:v") == "4000k"
    assert _option_value(command, "-c:a") == "aac"
    assert _option_value(command, "-b:a") == "192k"
    assert _option_value(command, "-ar") == "48000"
    assert _option_value(command, "-profile:v") == "high"
    assert _option_value(command, "-pix_fmt") == "yuv420p"
    assert _option_value(command, "-r") == "30"
    assert _option_value(command, "-g") == "120"
    assert _option_value(command, "-keyint_min") == "120"
    assert _option_value(command, "-sc_threshold") == "0"
    assert _option_value(command, "-force_key_frames") == "expr:gte(t,n_forced*4)"
    assert _option_value(command, "-f") == "hls"
    assert _option_value(command, "-hls_time") == "4"
    assert _option_value(command, "-hls_list_size") == "6"
    assert _option_value(command, "-hls_segment_filename") == "/hls/live-%09d.ts"
    assert command[-1] == "/hls/live.m3u8"

    flags = _option_value(command, "-hls_flags").split("+")
    assert set(flags) == {
        "delete_segments",
        "append_list",
        "discont_start",
        "omit_endlist",
        "temp_file",
    }
    assert "-hls_playlist_type" not in command
    assert "vod" not in command
    assert "EXT-X" + "-ENDLIST" not in " ".join(command)


def test_video_filter_fits_and_pads_to_1080p() -> None:
    command = build_ffmpeg_command(Path("/media/input.mkv"), ProducerConfig(), lambda _: True)

    assert _option_value(command, "-vf") == (
        "scale=w=1920:h=1080:force_original_aspect_ratio=decrease,"
        "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1"
    )


def test_normal_audio_maps_input_audio_without_silent_source() -> None:
    command = build_ffmpeg_command(Path("/media/with-audio.mov"), ProducerConfig(), lambda _: True)

    assert command.count("-i") == 1
    assert _map_values(command) == ["0:v:0", "0:a:0"]
    assert SILENT_AUDIO_SOURCE not in command
    assert "-shortest" not in command


def test_audio_less_input_uses_anullsrc() -> None:
    command = build_ffmpeg_command(Path("/media/no-audio.webm"), ProducerConfig(), lambda _: False)

    assert command.count("-i") == 2
    assert command[_option_index(command, "-f") + 1] == "lavfi"
    assert SILENT_AUDIO_SOURCE in command
    assert _map_values(command) == ["0:v:0", "1:a:0"]
    assert "-shortest" in command
    assert _option_value(command, "-c:a") == "aac"


def test_custom_config_controls_hls_and_gop_values() -> None:
    config = ProducerConfig(
        hls_dir=Path("/custom-hls"),
        hls_playlist="channel.m3u8",
        segment_seconds=5,
        playlist_size=8,
        output_width=1280,
        output_height=720,
        output_fps=24,
    )

    command = build_ffmpeg_command(Path("/media/input.mp4"), config, lambda _: True)

    assert _option_value(command, "-r") == "24"
    assert _option_value(command, "-g") == "120"
    assert _option_value(command, "-hls_time") == "5"
    assert _option_value(command, "-hls_list_size") == "8"
    assert _option_value(command, "-hls_segment_filename") == "/custom-hls/live-%09d.ts"
    assert _option_value(command, "-vf") == (
        "scale=w=1280:h=720:force_original_aspect_ratio=decrease,"
        "pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1"
    )
    assert command[-1] == "/custom-hls/channel.m3u8"


def test_input_has_audio_uses_ffprobe_json(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], bool, bool, bool]] = []

    class Result:
        stdout: str = '{"streams":[{"index":1}]}'

    def fake_run(args: list[str], check: bool, capture_output: bool, text: bool) -> Result:
        calls.append((args, check, capture_output, text))
        return Result()

    monkeypatch.setattr("app.ffmpeg.subprocess.run", fake_run)

    assert input_has_audio(Path("/media/input.mp4")) is True
    args, check, capture_output, text = calls[0]
    assert args == [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a",
        "-show_entries",
        "stream=index",
        "-of",
        "json",
        "/media/input.mp4",
    ]
    assert check is True
    assert capture_output is True
    assert text is True


def test_input_has_audio_returns_false_for_no_streams(monkeypatch: pytest.MonkeyPatch) -> None:
    class Result:
        stdout: str = '{"streams":[]}'

    def fake_run(args: list[str], check: bool, capture_output: bool, text: bool) -> Result:
        assert args[-1] == "/media/no-audio.mp4"
        assert check is True
        assert capture_output is True
        assert text is True
        return Result()

    monkeypatch.setattr("app.ffmpeg.subprocess.run", fake_run)

    assert input_has_audio(Path("/media/no-audio.mp4")) is False


def _option_index(command: list[str], option: str) -> int:
    return command.index(option)


def _option_value(command: list[str], option: str) -> str:
    return command[_option_index(command, option) + 1]


def _map_values(command: list[str]) -> list[str]:
    return [command[index + 1] for index, argument in enumerate(command) if argument == "-map"]
