import asyncio
import logging
from pathlib import Path

from config import HLS_LIST_SIZE, HLS_OUTPUT_DIR, HLS_SEGMENT_TIME, MAX_SEGMENT_AGE, SERVER_PORT


logger = logging.getLogger(__name__)


def audio_hls_source_url() -> str:
    return f'http://127.0.0.1:{SERVER_PORT}/live/stream.m3u8'


def audio_hls_dir() -> Path:
    return Path(HLS_OUTPUT_DIR) / 'audio'


def audio_hls_delete_threshold() -> int:
    return max(1, int(MAX_SEGMENT_AGE) // int(HLS_SEGMENT_TIME))


def build_audio_hls_command() -> list[str]:
    output_dir = audio_hls_dir()
    return [
        'ffmpeg',
        '-y',
        '-i', audio_hls_source_url(),
        '-map', '0:a:0',
        '-vn',
        '-c:a', 'copy',
        '-f', 'hls',
        '-hls_time', str(HLS_SEGMENT_TIME),
        '-hls_list_size', str(HLS_LIST_SIZE),
        '-hls_delete_threshold', str(audio_hls_delete_threshold()),
        '-hls_flags', 'delete_segments+temp_file+omit_endlist',
        '-hls_segment_type', 'mpegts',
        '-hls_start_number_source', 'epoch',
        '-hls_base_url', 'audio/',
        '-hls_segment_filename', str(output_dir / 'audio_%05d.ts'),
        str(output_dir / 'audio.m3u8'),
    ]


class AudioHLSRunner:
    def __init__(self):
        self._process: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task[None] | None = None

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def start(self) -> bool:
        if self.is_running:
            return True

        self._prepare_output_dir()
        command = build_audio_hls_command()

        logger.info('Starting audio HLS sidecar: input=%s', audio_hls_source_url())
        try:
            self._process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as e:
            logger.error('Failed to start audio HLS sidecar: %s', e)
            self._process = None
            return False

        self._stderr_task = asyncio.create_task(self._drain_stderr())
        return True

    async def stop(self, graceful_timeout: float = 5.0) -> int | None:
        if not self._process:
            return None

        exit_code = self._process.returncode
        if exit_code is None:
            logger.info('Stopping audio HLS sidecar')
            self._process.terminate()
            try:
                exit_code = await asyncio.wait_for(self._process.wait(), timeout=graceful_timeout)
            except asyncio.TimeoutError:
                logger.warning('Audio HLS sidecar did not terminate, killing')
                self._process.kill()
                exit_code = await self._process.wait()

        await self._stop_stderr_task()
        self._process = None
        logger.info('Audio HLS sidecar stopped with exit code %s', exit_code)
        return exit_code

    async def wait(self) -> int | None:
        if not self._process:
            return None
        return await self._process.wait()

    def _prepare_output_dir(self) -> None:
        output_dir = audio_hls_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        for stale_file in output_dir.glob('audio*.ts'):
            stale_file.unlink(missing_ok=True)
        playlist = output_dir / 'audio.m3u8'
        playlist.unlink(missing_ok=True)

    async def _stop_stderr_task(self) -> None:
        if not self._stderr_task:
            return
        _ = self._stderr_task.cancel()
        try:
            await self._stderr_task
        except asyncio.CancelledError:
            pass
        self._stderr_task = None

    async def _drain_stderr(self) -> None:
        if not self._process or not self._process.stderr:
            return

        try:
            while True:
                line = await self._process.stderr.readline()
                if not line:
                    break
                decoded = line.decode().rstrip()
                if decoded:
                    logger.debug('audio ffmpeg: %s', decoded)
        except asyncio.CancelledError:
            pass
        except OSError as e:
            logger.debug('Audio HLS stderr drain error: %s', e)
