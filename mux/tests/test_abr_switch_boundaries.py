# pyright: reportMissingImports=false, reportPrivateUsage=false, reportAny=false, reportImplicitOverride=false, reportUnusedCallResult=false, reportAttributeAccessIssue=false

import importlib
import os
import sys
import unittest
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1] / 'app'
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


MUX_MODULES = ['config', 'segment_store', 'ffmpeg_runner']


def load_module(name: str):
    return importlib.import_module(name)


class ABRSwitchBoundaryTest(unittest.IsolatedAsyncioTestCase):
    _env_backup: dict[str, str] = {}

    def setUp(self):
        self._env_backup = os.environ.copy()
        os.environ['MUX_MODE'] = 'abr'
        for module_name in MUX_MODULES:
            sys.modules.pop(module_name, None)

    def tearDown(self):
        for module_name in MUX_MODULES:
            sys.modules.pop(module_name, None)
        os.environ.clear()
        os.environ.update(self._env_backup)

    async def test_discontinuity_is_emitted_for_each_variant_playlist(self):
        segment_store_module = load_module('segment_store')
        store = segment_store_module.SegmentStore()

        await store.add_segment(0, 'segment_00000.ts', 4.0)
        await store.add_segment(1, 'segment_00000.ts', 4.0)
        await store.mark_discontinuity()
        await store.add_segment(0, 'segment_00001.ts', 4.0)
        await store.add_segment(1, 'segment_00001.ts', 4.0)

        source_playlist = await store.generate_playlist(0)
        fallback_playlist = await store.generate_playlist(1)

        self.assertIn('#EXT-X-DISCONTINUITY\n#EXTINF:4.000,\nsegment_00001.ts', source_playlist)
        self.assertIn('#EXT-X-DISCONTINUITY\n#EXTINF:4.000,\nsegment_00001.ts', fallback_playlist)


    async def test_duplicate_discontinuity_mark_is_idempotent(self):
        segment_store_module = load_module('segment_store')
        store = segment_store_module.SegmentStore()

        await store.add_segment(0, 'segment_00000.ts', 4.0)
        await store.add_segment(1, 'segment_00000.ts', 4.0)
        await store.mark_discontinuity()
        await store.mark_discontinuity()
        await store.add_segment(0, 'segment_00001.ts', 4.0)
        await store.add_segment(1, 'segment_00001.ts', 4.0)

        source_segments = await store.get_segments(0)
        fallback_segments = await store.get_segments(1)
        source_playlist = await store.generate_playlist(0)

        self.assertEqual(1, source_segments[-1].discontinuity_sequence)
        self.assertEqual(1, fallback_segments[-1].discontinuity_sequence)
        self.assertEqual(1, source_playlist.count('#EXT-X-DISCONTINUITY\n'))

    async def test_discontinuity_moves_to_first_aligned_sequence_after_skip(self):
        segment_store_module = load_module('segment_store')
        store = segment_store_module.SegmentStore()

        await store.add_segment(0, 'segment_00111.ts', 4.0)
        await store.add_segment(1, 'segment_00111.ts', 4.0)
        await store.mark_discontinuity()
        await store.add_segment(0, 'segment_00115.ts', 4.0)
        await store.add_segment(1, 'segment_00115.ts', 4.0)

        source_playlist = await store.generate_playlist(0)
        fallback_playlist = await store.generate_playlist(1)

        self.assertIn('#EXT-X-DISCONTINUITY\n#EXTINF:4.000,\nsegment_00115.ts', source_playlist)
        self.assertIn('#EXT-X-DISCONTINUITY\n#EXTINF:4.000,\nsegment_00115.ts', fallback_playlist)
        self.assertNotIn(112, store._discontinuity_sequences)
        self.assertEqual(1, store._discontinuity_sequences[115])

    async def test_abr_start_segments_are_registered_together(self):
        ffmpeg_runner_module = load_module('ffmpeg_runner')
        registered: list[tuple[int, str, float]] = []

        async def on_segment(variant: int, filename: str, duration: float) -> None:
            registered.append((variant, filename, duration))

        runner = ffmpeg_runner_module.FFmpegRunner(on_segment=on_segment)
        runner._start_number = 7

        await runner._register_start_segment(0, 7, 'segment_00007.ts', 4.0)

        self.assertEqual([], registered)
        self.assertFalse(runner._has_required_start_segments(0))

        await runner._register_start_segment(1, 7, 'segment_00007.ts', 4.0)

        self.assertEqual(
            [(0, 'segment_00007.ts', 4.0), (1, 'segment_00007.ts', 4.0)],
            registered,
        )
        self.assertTrue(runner._has_required_start_segments(0))


    async def test_discontinuity_pruning_preserves_sequence_after_memory_trim(self):
        segment_store_module = load_module('segment_store')
        original_cap = segment_store_module.MAX_SEGMENTS_IN_MEMORY
        segment_store_module.MAX_SEGMENTS_IN_MEMORY = 3
        try:
            store = segment_store_module.SegmentStore()

            for sequence in range(2):
                await store.add_segment(0, f'segment_{sequence:05d}.ts', 4.0)
                await store.add_segment(1, f'segment_{sequence:05d}.ts', 4.0)
                await store.mark_discontinuity()

            for sequence in range(2, 6):
                await store.add_segment(0, f'segment_{sequence:05d}.ts', 4.0)
                await store.add_segment(1, f'segment_{sequence:05d}.ts', 4.0)

            source_playlist = await store.generate_playlist(0)

            self.assertEqual({}, store._discontinuity_sequences)
            self.assertEqual(2, store._discontinuity_sequence_floor)
            self.assertIn('#EXT-X-DISCONTINUITY-SEQUENCE:2', source_playlist)
        finally:
            segment_store_module.MAX_SEGMENTS_IN_MEMORY = original_cap

    async def test_discontinuity_pruning_preserves_sequence_after_age_cleanup(self):
        segment_store_module = load_module('segment_store')
        store = segment_store_module.SegmentStore()

        for sequence in range(2):
            source = await store.add_segment(0, f'segment_{sequence:05d}.ts', 4.0)
            fallback = await store.add_segment(1, f'segment_{sequence:05d}.ts', 4.0)
            if source:
                source.created_at = 0.0
            if fallback:
                fallback.created_at = 0.0
            await store.mark_discontinuity()

        source = await store.add_segment(0, 'segment_00002.ts', 4.0)
        fallback = await store.add_segment(1, 'segment_00002.ts', 4.0)
        if source:
            source.created_at = 0.0
        if fallback:
            fallback.created_at = 0.0
        await store.add_segment(0, 'segment_00003.ts', 4.0)
        await store.add_segment(1, 'segment_00003.ts', 4.0)

        removed = await store.cleanup_old_segments()
        source_playlist = await store.generate_playlist(0)

        self.assertEqual(6, removed)
        self.assertEqual({}, store._discontinuity_sequences)
        self.assertEqual(2, store._discontinuity_sequence_floor)
        self.assertIn('#EXT-X-DISCONTINUITY-SEQUENCE:2', source_playlist)

    async def test_abr_start_alignment_uses_first_common_sequence_after_start(self):
        ffmpeg_runner_module = load_module('ffmpeg_runner')
        registered: list[tuple[int, str, float]] = []

        async def on_segment(variant: int, filename: str, duration: float) -> None:
            registered.append((variant, filename, duration))

        runner = ffmpeg_runner_module.FFmpegRunner(on_segment=on_segment)
        runner._start_number = 112

        await runner._register_start_segment(1, 113, 'segment_00113.ts', 4.0)
        await runner._register_start_segment(0, 115, 'segment_00115.ts', 4.0)
        await runner._register_start_segment(1, 116, 'segment_00116.ts', 4.0)

        self.assertEqual([], registered)
        self.assertFalse(runner._has_required_start_segments(0))

        await runner._register_start_segment(1, 115, 'segment_00115.ts', 4.0)

        self.assertEqual(
            [(0, 'segment_00115.ts', 4.0), (1, 'segment_00115.ts', 4.0)],
            registered,
        )
        self.assertTrue(runner._has_required_start_segments(0))

        await runner._register_start_segment(0, 116, 'segment_00116.ts', 4.0)

        self.assertEqual(
            [
                (0, 'segment_00115.ts', 4.0),
                (1, 'segment_00115.ts', 4.0),
                (0, 'segment_00116.ts', 4.0),
                (1, 'segment_00116.ts', 4.0),
            ],
            registered,
        )

    def test_abr_readiness_requires_common_sequence_from_every_variant(self):
        ffmpeg_runner_module = load_module('ffmpeg_runner')
        runner = ffmpeg_runner_module.FFmpegRunner()
        runner._start_number = 7

        runner._segments_by_sequence = {7: {0}}
        self.assertFalse(runner._has_required_start_segments(0))

        runner._segments_by_sequence = {7: {0}, 8: {1}}
        self.assertFalse(runner._has_required_start_segments(0))

        runner._segments_by_sequence = {8: {0, 1}}
        self.assertTrue(runner._has_required_start_segments(0))


    def test_copy_readiness_requires_registered_valid_segment(self):
        ffmpeg_runner_module = load_module('ffmpeg_runner')
        original_variants = ffmpeg_runner_module.NUM_VARIANTS
        ffmpeg_runner_module.NUM_VARIANTS = 1
        try:
            runner = ffmpeg_runner_module.FFmpegRunner()
            runner._start_number = 7
            runner._known_segments = {'/tmp/hls/segment_00006.ts'}

            self.assertFalse(runner._has_required_start_segments(0))

            runner._segments_by_sequence = {7: {0}}

            self.assertTrue(runner._has_required_start_segments(0))
        finally:
            ffmpeg_runner_module.NUM_VARIANTS = original_variants


if __name__ == '__main__':
    unittest.main()
