import importlib
import sys
import types
import unittest
from pathlib import Path
from typing import Awaitable, Callable, override


APP_DIR = Path(__file__).resolve().parents[1] / 'app'
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


class FakeStreamInfo:
    width: int = 1920
    height: int = 1080
    bitrate: int = 8_000_000


class FakeSegmentStore:
    def __init__(self):
        self.discontinuities: int = 0
        self.source_infos: list[tuple[int, int, int]] = []

    async def get_next_sequence(self) -> int:
        return 7

    async def set_source_info(self, width: int, height: int, bitrate: int) -> None:
        self.source_infos.append((width, height, bitrate))

    async def add_segment(self, variant: int, filename: str, duration: float) -> None:
        _ = (variant, filename, duration)

    async def mark_discontinuity(self) -> None:
        self.discontinuities += 1


class FakeFFmpegRunner:
    instances: list['FakeFFmpegRunner'] = []
    start_result: bool = True
    segment_result: bool = True

    def __init__(self, on_segment: Callable[[int, str, float], Awaitable[None]] | None = None):
        self.on_segment: Callable[[int, str, float], Awaitable[None]] | None = on_segment
        self.stream_info: FakeStreamInfo = FakeStreamInfo()
        self.is_running: bool = True
        self.starts: list[tuple[str, int]] = []
        self.stops: int = 0
        FakeFFmpegRunner.instances.append(self)

    async def start(self, url: str, start_number: int = 0) -> bool:
        self.starts.append((url, start_number))
        return self.start_result

    async def wait_for_segment(self, timeout: float = 15.0) -> bool:
        _ = timeout
        return self.segment_result

    async def stop(self) -> int:
        self.stops += 1
        self.is_running = False
        return 0

    async def stop_graceful(self, timeout: float = 10.0) -> int:
        _ = timeout
        self.stops += 1
        self.is_running = False
        return 0

    async def wait(self) -> int:
        return 1


class FakeAudioRunner:
    instances: list['FakeAudioRunner'] = []
    start_result: bool = True

    def __init__(self):
        self.starts: int = 0
        self.stops: int = 0
        self.is_running: bool = False
        FakeAudioRunner.instances.append(self)

    async def start(self) -> bool:
        self.starts += 1
        self.is_running = self.start_result
        return self.start_result

    async def stop(self) -> int:
        self.stops += 1
        self.is_running = False
        return 0


class StreamManagerAudioSidecarTest(unittest.IsolatedAsyncioTestCase):
    module: types.ModuleType = types.ModuleType('stream-manager-placeholder')

    @override
    def setUp(self):
        _ = sys.modules.pop('stream_manager', None)
        self.module = importlib.import_module('stream_manager')
        FakeFFmpegRunner.instances = []
        FakeFFmpegRunner.start_result = True
        FakeFFmpegRunner.segment_result = True
        FakeAudioRunner.instances = []
        FakeAudioRunner.start_result = True
        setattr(self.module, 'FFmpegRunner', FakeFFmpegRunner)
        setattr(self.module, 'AudioHLSRunner', FakeAudioRunner)
        setattr(self.module, 'segment_store', FakeSegmentStore())
        setattr(self.module, 'setup_output_dirs', lambda: None)

    @override
    def tearDown(self):
        _ = sys.modules.pop('stream_manager', None)

    async def test_audio_sidecar_starts_after_primary_segment_is_ready(self):
        manager = getattr(self.module, 'StreamManager')()

        started = await manager._try_start_ffmpeg('https://example.test/live.m3u8')

        self.assertTrue(started)
        self.assertEqual([('https://example.test/live.m3u8', 7)], FakeFFmpegRunner.instances[0].starts)
        self.assertEqual(1, FakeAudioRunner.instances[0].starts)

    async def test_audio_sidecar_failure_does_not_fail_primary_video_start(self):
        FakeAudioRunner.start_result = False
        manager = getattr(self.module, 'StreamManager')()

        started = await manager._try_start_ffmpeg('https://example.test/live.m3u8')

        self.assertTrue(started)
        self.assertEqual(1, FakeAudioRunner.instances[0].starts)
        self.assertTrue(FakeFFmpegRunner.instances[0].is_running)

    async def test_stop_stops_audio_sidecar_and_primary_runner(self):
        manager = getattr(self.module, 'StreamManager')()
        started = await manager.start('https://example.test/live.m3u8')

        await manager.stop()

        self.assertTrue(started)
        self.assertEqual(1, FakeAudioRunner.instances[0].stops)
        self.assertEqual(1, FakeFFmpegRunner.instances[0].stops)

    async def test_recovery_restarts_dead_audio_sidecar_without_restarting_video(self):
        manager = getattr(self.module, 'StreamManager')()
        started = await manager.start('https://example.test/live.m3u8')
        FakeAudioRunner.instances[0].is_running = False

        await manager._check_and_recover()

        self.assertTrue(started)
        self.assertEqual(2, FakeAudioRunner.instances[0].starts)
        self.assertEqual(1, len(FakeFFmpegRunner.instances))


if __name__ == '__main__':
    _ = unittest.main()
