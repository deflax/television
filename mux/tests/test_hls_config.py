# pyright: reportMissingImports=false, reportImplicitOverride=false, reportAny=false, reportUnusedCallResult=false

import importlib
import os
import sys
import unittest
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1] / 'app'
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


class HLSConfigTest(unittest.TestCase):
    _env_backup: dict[str, str] = {}

    def setUp(self):
        self._env_backup = os.environ.copy()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env_backup)
        sys.modules.pop('config', None)

    def _load_config(self):
        sys.modules.pop('config', None)
        return importlib.import_module('config')

    def test_default_segment_retention_covers_cache_lifetime(self):
        config = self._load_config()

        self.assertEqual(config.HLS_SEGMENT_CACHE_MAX_AGE, 300)
        self.assertEqual(config.HLS_SEGMENT_CACHE_STALE_REVALIDATE, 60)
        self.assertEqual(config.MAX_SEGMENT_AGE, 440)
        self.assertEqual(
            config.SEGMENT_CACHE_CONTROL,
            'public, max-age=300, stale-while-revalidate=60',
        )

    def test_segment_retention_is_clamped_to_cache_lifetime(self):
        os.environ['HLS_SEGMENT_RETENTION_SECONDS'] = '120'

        config = self._load_config()

        self.assertEqual(config.MAX_SEGMENT_AGE, 440)

    def test_segment_cache_settings_are_configurable(self):
        os.environ['HLS_SEGMENT_CACHE_MAX_AGE'] = '120'
        os.environ['HLS_SEGMENT_CACHE_STALE_REVALIDATE'] = '30'
        os.environ['HLS_SEGMENT_RETENTION_SECONDS'] = '240'

        config = self._load_config()

        self.assertEqual(config.MAX_SEGMENT_AGE, 240)
        self.assertEqual(
            config.SEGMENT_CACHE_CONTROL,
            'public, max-age=120, stale-while-revalidate=30',
        )

    def test_playlist_window_can_control_default_retention(self):
        os.environ['HLS_SEGMENT_TIME'] = '10'
        os.environ['HLS_LIST_SIZE'] = '100'
        os.environ['HLS_SEGMENT_CACHE_MAX_AGE'] = '120'
        os.environ['HLS_SEGMENT_CACHE_STALE_REVALIDATE'] = '30'

        config = self._load_config()

        self.assertEqual(config.MAX_SEGMENT_AGE, 3000)

    def test_segment_store_memory_cap_covers_retention_age(self):
        sys.modules.pop('segment_store', None)
        config = self._load_config()
        segment_store = importlib.import_module('segment_store')

        expected_minimum = int(config.MAX_SEGMENT_AGE / config.HLS_SEGMENT_TIME) + 1
        self.assertGreaterEqual(segment_store.MAX_SEGMENTS_IN_MEMORY, expected_minimum)
        sys.modules.pop('segment_store', None)


if __name__ == '__main__':
    unittest.main()
