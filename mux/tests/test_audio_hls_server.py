import importlib
import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from typing import override


APP_DIR = Path(__file__).resolve().parents[1] / 'app'
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

QUART_AVAILABLE = importlib.util.find_spec('quart') is not None


@unittest.skipUnless(QUART_AVAILABLE, 'quart is required for mux HTTP route tests')
class AudioHLSServerRouteTest(unittest.IsolatedAsyncioTestCase):
    server: types.ModuleType = types.ModuleType('server-placeholder')
    tracker_module: types.ModuleType = types.ModuleType('tracker-placeholder')

    @override
    def setUp(self):
        _ = sys.modules.pop('server', None)
        self.server = importlib.import_module('server')
        self.tracker_module = importlib.import_module('hls_viewer_tracker')

    @override
    def tearDown(self):
        _ = sys.modules.pop('server', None)

    async def test_audio_playlist_is_served_from_audio_directory_and_tracks_hls_viewer(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            audio_dir = Path(temp_dir) / 'audio'
            audio_dir.mkdir()
            playlist = '#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4.000,\naudio_00001.ts\n'
            _ = (audio_dir / 'audio.m3u8').write_text(playlist, encoding='utf-8')
            setattr(self.server, 'HLS_OUTPUT_DIR', temp_dir)
            setattr(self.server, 'hls_viewer_tracker', self.tracker_module.HLSViewerTracker())

            response = await self.server.app.test_client().get(
                '/live/audio.m3u8',
                headers={'X-Forwarded-For': '8.8.8.8'},
            )
            body = await response.get_data(as_text=True)
            count = await self.server.hls_viewer_tracker.count

        self.assertEqual(200, response.status_code)
        self.assertEqual(playlist, body)
        self.assertEqual('application/vnd.apple.mpegurl', response.mimetype)
        self.assertEqual('*', response.headers['Access-Control-Allow-Origin'])
        self.assertEqual('no-store', response.headers['CDN-Cache-Control'])
        self.assertIn('no-cache', response.headers['Cache-Control'])
        self.assertEqual(1, count)

    async def test_audio_segment_uses_stable_file_serving_and_segment_cache_headers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            audio_dir = Path(temp_dir) / 'audio'
            audio_dir.mkdir()
            segment = audio_dir / 'audio_00001.ts'
            _ = segment.write_bytes(b'fake-ts-data')
            setattr(self.server, 'HLS_OUTPUT_DIR', temp_dir)
            original_wait = self.server.wait_for_stable_file

            async def stable_file(path: Path, check_delay: float = 0.1, max_attempts: int = 10) -> bool:
                _ = check_delay
                _ = max_attempts
                self.assertEqual(segment, path)
                return True

            setattr(self.server, 'wait_for_stable_file', stable_file)
            try:
                response = await self.server.app.test_client().get('/live/audio/audio_00001.ts')
                body = await response.get_data()
            finally:
                setattr(self.server, 'wait_for_stable_file', original_wait)

        self.assertEqual(200, response.status_code)
        self.assertEqual(b'fake-ts-data', body)
        self.assertEqual('video/mp2t', response.mimetype)
        self.assertEqual('*', response.headers['Access-Control-Allow-Origin'])
        self.assertIn('max-age', response.headers['Cache-Control'])

    async def test_audio_segment_rejects_non_ts_and_path_traversal(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            audio_dir = Path(temp_dir) / 'audio'
            audio_dir.mkdir()
            outside_segment = Path(temp_dir) / 'outside.ts'
            _ = outside_segment.write_bytes(b'outside')
            setattr(self.server, 'HLS_OUTPUT_DIR', temp_dir)
            client = self.server.app.test_client()

            non_ts = await client.get('/live/audio/not-a-segment.m3u8')
            traversal = await client.get('/live/audio/%2e%2e/outside.ts')

        self.assertEqual(404, non_ts.status_code)
        self.assertEqual(403, traversal.status_code)


if __name__ == '__main__':
    _ = unittest.main()
