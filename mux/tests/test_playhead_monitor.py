# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportAttributeAccessIssue=false, reportAny=false, reportUnknownLambdaType=false, reportUnannotatedClassAttribute=false

import importlib
import json
import sys
import types
import unittest
from pathlib import Path
from typing import override


APP_DIR = Path(__file__).resolve().parents[1] / 'app'
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


class PlayheadMonitorTest(unittest.IsolatedAsyncioTestCase):
    module: types.ModuleType = types.ModuleType('playhead-monitor-placeholder')

    @override
    def setUp(self):
        _ = sys.modules.pop('playhead_monitor', None)
        self.module = importlib.import_module('playhead_monitor')
        setattr(self.module, 'rewrite_stream_url', lambda url: url)

    @override
    def tearDown(self):
        _ = sys.modules.pop('playhead_monitor', None)

    async def test_metadata_only_playhead_update_does_not_trigger_stream_change(self):
        changes: list[tuple[str, str]] = []

        async def on_change(new_url: str, stream_name: str) -> None:
            changes.append((new_url, stream_name))

        monitor = self.module.PlayheadMonitor(on_change=on_change)

        await monitor._handle_line(
            'data: ' + json.dumps(
                {
                    'head': 'https://example.test/live-a.m3u8',
                    'name': 'Channel A',
                    'metadata': {'stream_title': 'Artist - First'},
                }
            )
        )
        await monitor._handle_line(
            'data: ' + json.dumps(
                {
                    'head': 'https://example.test/live-a.m3u8',
                    'name': 'Channel A',
                    'metadata': {'stream_title': 'Artist - Second'},
                }
            )
        )
        await monitor._handle_line(
            'data: ' + json.dumps(
                {
                    'head': 'https://example.test/live-b.m3u8',
                    'name': 'Channel B',
                    'metadata': {'stream_title': 'Artist - Third'},
                }
            )
        )

        self.assertEqual(
            changes,
            [
                ('https://example.test/live-a.m3u8', 'Channel A'),
                ('https://example.test/live-b.m3u8', 'Channel B'),
            ],
        )


if __name__ == '__main__':
    _ = unittest.main()
