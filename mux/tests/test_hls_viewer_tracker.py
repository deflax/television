# pyright: reportMissingImports=false, reportUnusedCallResult=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false

import asyncio
import sys
import unittest
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1] / 'app'
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import hls_viewer_tracker as tracker_module


class HLSViewerTrackerTest(unittest.TestCase):
    def test_logs_connect_once_for_new_viewer(self):
        tracker = tracker_module.HLSViewerTracker()
        original_monotonic = tracker_module.time.monotonic

        try:
            tracker_module.time.monotonic = lambda: 100.0

            with self.assertLogs('hls_viewer_tracker', level='INFO') as logs:
                asyncio.run(tracker.record_playlist_fetch('8.8.8.8'))

            self.assertEqual(
                logs.output,
                ['INFO:hls_viewer_tracker:HLS viewer connected: ip=8.8.8.8 active=1'],
            )

            with self.assertNoLogs('hls_viewer_tracker', level='INFO'):
                asyncio.run(tracker.record_playlist_fetch('8.8.8.8'))
        finally:
            tracker_module.time.monotonic = original_monotonic

    def test_logs_disconnect_when_viewer_expires(self):
        tracker = tracker_module.HLSViewerTracker()
        original_monotonic = tracker_module.time.monotonic

        try:
            tracker_module.time.monotonic = lambda: 100.0
            asyncio.run(tracker.record_playlist_fetch('8.8.8.8'))

            tracker_module.time.monotonic = lambda: 131.0

            with self.assertLogs('hls_viewer_tracker', level='INFO') as logs:
                asyncio.run(tracker.cleanup_expired())

            self.assertEqual(
                logs.output,
                [
                    ''.join([
                        'INFO:hls_viewer_tracker:HLS viewer disconnected: ',
                        'ip=8.8.8.8 active=0 reason=ttl_expired',
                    ])
                ],
            )
        finally:
            tracker_module.time.monotonic = original_monotonic

    def test_count_and_viewers_prune_expired_entries_on_read(self):
        tracker = tracker_module.HLSViewerTracker()
        original_monotonic = tracker_module.time.monotonic

        async def read_count() -> int:
            return await tracker.count

        async def read_viewers() -> dict[str, float]:
            return await tracker.viewers

        try:
            tracker_module.time.monotonic = lambda: 100.0
            asyncio.run(tracker.record_playlist_fetch('8.8.8.8'))

            tracker_module.time.monotonic = lambda: 131.0

            self.assertEqual(asyncio.run(read_count()), 0)
            self.assertEqual(asyncio.run(read_viewers()), {})
        finally:
            tracker_module.time.monotonic = original_monotonic

    def test_private_ips_are_ignored(self):
        tracker = tracker_module.HLSViewerTracker()
        original_monotonic = tracker_module.time.monotonic

        async def read_count() -> int:
            return await tracker.count

        async def read_viewers() -> dict[str, float]:
            return await tracker.viewers

        try:
            tracker_module.time.monotonic = lambda: 100.0

            asyncio.run(tracker.record_playlist_fetch('10.0.0.5'))
            asyncio.run(tracker.record_playlist_fetch('127.0.0.1'))
            asyncio.run(tracker.record_playlist_fetch('8.8.8.8'))

            self.assertEqual(asyncio.run(read_count()), 1)
            self.assertEqual(set(asyncio.run(read_viewers()).keys()), {'8.8.8.8'})
        finally:
            tracker_module.time.monotonic = original_monotonic


if __name__ == '__main__':
    unittest.main()
