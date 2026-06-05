# pyright: reportImplicitRelativeImport=false

import sys
import types
import unittest
from pathlib import Path

api_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(api_dir))
web_dir = api_dir / 'web'
web_module = types.ModuleType('web')
web_module.__path__ = [str(web_dir)]
_ = sys.modules.setdefault('web', web_module)

from web.state import (
    HLS_VIEWER_DISPLAY_GRACE_SECONDS,
    WebRouteState,
    apply_hls_viewer_display_grace,
)
from web.timecode_manager import TimecodeManager
from web.visitor_tracker import VisitorTracker


def make_state() -> WebRouteState:
    return WebRouteState(
        timecode_manager=TimecodeManager(),
        visitor_tracker=VisitorTracker(),
    )


class HLSViewerDisplayGraceTest(unittest.TestCase):
    def test_grace_constant_is_240_seconds(self):
        self.assertEqual(HLS_VIEWER_DISPLAY_GRACE_SECONDS, 240.0)

    def test_new_viewers_appear_immediately(self):
        state = make_state()

        displayed = apply_hls_viewer_display_grace(state, {'8.8.8.8', '1.1.1.1'}, 100.0)

        self.assertEqual(displayed, {'8.8.8.8', '1.1.1.1'})

    def test_missing_viewer_stays_displayed_within_grace(self):
        state = make_state()

        _ = apply_hls_viewer_display_grace(state, {'8.8.8.8', '1.1.1.1'}, 100.0)
        displayed = apply_hls_viewer_display_grace(state, {'8.8.8.8'}, 200.0)

        self.assertEqual(displayed, {'8.8.8.8', '1.1.1.1'})

    def test_missing_viewer_drops_after_display_grace(self):
        state = make_state()

        _ = apply_hls_viewer_display_grace(state, {'8.8.8.8', '1.1.1.1'}, 100.0)
        displayed = apply_hls_viewer_display_grace(state, {'8.8.8.8'}, 341.0)

        self.assertEqual(displayed, {'8.8.8.8'})

    def test_empty_report_keeps_viewers_until_grace_expires(self):
        state = make_state()

        _ = apply_hls_viewer_display_grace(state, {'8.8.8.8'}, 100.0)
        displayed = apply_hls_viewer_display_grace(state, set(), 339.0)
        self.assertEqual(displayed, {'8.8.8.8'})

        displayed = apply_hls_viewer_display_grace(state, set(), 341.0)
        self.assertEqual(displayed, set())


if __name__ == '__main__':
    _ = unittest.main()
