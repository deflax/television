import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path
from typing import override


APP_DIR = Path(__file__).resolve().parents[1] / 'app'
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


class FakeStderr:
    async def readline(self) -> bytes:
        return b''


class FakeProcess:
    def __init__(self):
        self.returncode: int | None = None
        self.stderr: FakeStderr = FakeStderr()
        self.terminated: bool = False
        self.killed: bool = False

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode if self.returncode is not None else 0


class AudioHLSSidecarTest(unittest.IsolatedAsyncioTestCase):
    module: types.ModuleType = types.ModuleType('audio-sidecar-placeholder')

    @override
    def setUp(self):
        _ = sys.modules.pop('audio_hls_sidecar', None)
        self.module = importlib.import_module('audio_hls_sidecar')

    @override
    def tearDown(self):
        _ = sys.modules.pop('audio_hls_sidecar', None)

    def test_build_command_uses_mux_output_as_audio_only_input(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            setattr(self.module, 'HLS_OUTPUT_DIR', temp_dir)
            setattr(self.module, 'SERVER_PORT', 18091)
            setattr(self.module, 'MAX_SEGMENT_AGE', 440)
            setattr(self.module, 'HLS_SEGMENT_TIME', 4)

            command = self.module.build_audio_hls_command()

        self.assertIn('http://127.0.0.1:18091/live/stream.m3u8', command)
        self.assertIn('-vn', command)
        self.assertIn('-map', command)
        self.assertIn('0:a:0', command)
        self.assertIn('-c:a', command)
        self.assertIn('copy', command)
        self.assertIn('-f', command)
        self.assertIn('hls', command)
        self.assertIn('-hls_segment_filename', command)
        self.assertIn('-hls_base_url', command)
        self.assertIn('audio/', command)
        self.assertIn('-hls_delete_threshold', command)
        self.assertIn('110', command)
        self.assertIn(f'{temp_dir}/audio/audio_%05d.ts', command)
        self.assertEqual(f'{temp_dir}/audio/audio.m3u8', command[-1])
        self.assertNotIn('-var_stream_map', command)
        self.assertFalse(any('libx264' in part for part in command))
        self.assertFalse(any('stream_%v' in part for part in command))

    async def test_start_and_stop_manage_one_shared_ffmpeg_process(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            captured_commands: list[list[str]] = []
            fake_process = FakeProcess()
            setattr(self.module, 'HLS_OUTPUT_DIR', temp_dir)
            setattr(self.module, 'SERVER_PORT', 18091)
            original_create = self.module.asyncio.create_subprocess_exec

            async def fake_create_subprocess_exec(*command: str, **_kwargs: int) -> FakeProcess:
                captured_commands.append(list(command))
                return fake_process

            setattr(self.module.asyncio, 'create_subprocess_exec', fake_create_subprocess_exec)
            try:
                runner = self.module.AudioHLSRunner()

                started = await runner.start()
                exit_code = await runner.stop()
            finally:
                setattr(self.module.asyncio, 'create_subprocess_exec', original_create)

        self.assertTrue(started)
        self.assertEqual(0, exit_code)
        self.assertFalse(runner.is_running)
        self.assertTrue(fake_process.terminated)
        self.assertFalse(fake_process.killed)
        self.assertEqual(1, len(captured_commands))
        self.assertIn('http://127.0.0.1:18091/live/stream.m3u8', captured_commands[0])


if __name__ == '__main__':
    _ = unittest.main()
