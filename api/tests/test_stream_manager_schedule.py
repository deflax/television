import importlib.util
import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


def load_stream_manager_module():
    services = types.ModuleType('services')
    services.__path__ = [str(Path(__file__).resolve().parents[1] / 'services')]
    core = types.ModuleType('services.core_api')
    core.CoreAPIClient = object
    metadata = types.ModuleType('services.playhead_metadata')
    metadata.PlayheadMetadataPoller = lambda *args: None
    requests = types.ModuleType('requests')
    requests.RequestException = RuntimeError
    requests.get = lambda *args, **kwargs: None
    background = types.ModuleType('apscheduler.schedulers.background')
    background.BackgroundScheduler = object
    modules = {'services': services, 'services.core_api': core,
               'services.playhead_metadata': metadata, 'requests': requests,
               'apscheduler': types.ModuleType('apscheduler'),
               'apscheduler.schedulers': types.ModuleType('apscheduler.schedulers'),
               'apscheduler.schedulers.background': background}
    with patch.dict(sys.modules, modules):
        path = Path(services.__path__[0]) / 'stream_manager.py'
        spec = importlib.util.spec_from_file_location('weekly_manager_under_test', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


module = load_stream_manager_module()


class Clock(datetime):
    current = datetime(2026, 11, 2, 0, 0, 5, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls.current if tz else cls.current.replace(tzinfo=None)


class Core:
    def __init__(self, *channels):
        self.channels = {item['id']: item for item in channels}
        self.commands = []
        self.fail_commands = False

    def v3_process_get_list(self):
        return [{'id': key} for key in self.channels]

    def v3_process_get(self, id):
        return self.channels[id]

    def v3_process_put_command(self, id, command):
        self.commands.append((id, command))
        if self.fail_commands:
            raise RuntimeError('unavailable')
        self.channels[id]['state']['order'] = command


def channel(name='weekly', days=('mon',), start='0000', running=False, priority=0):
    settings = {'start_at': start, 'prio': priority}
    if days is not None:
        settings['days'] = list(days)
    return {
        'id': f'restreamer-ui:ingest:{name}', 'reference': name,
        'state': {'exec': 'running' if running else 'finished',
                  'order': 'start' if running else 'stop'},
        'metadata': {'restreamer-ui': {
            'meta': {'name': name, 'description': json.dumps(settings)},
            'control': {'hls': {'storage': 'memfs'}},
        }},
    }


class WeeklyControlTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = types.SimpleNamespace(core_hostname='core.test',
            schedule_state_path=str(Path(self.directory.name) / 'schedule.json'))
        Clock.current = datetime(2026, 11, 2, 0, 0, 5, tzinfo=timezone.utc)
        self.clock_patch = patch.object(module, 'datetime', Clock)
        self.clock_patch.start()
        self.addCleanup(self.clock_patch.stop)

    def manager(self, core):
        manager = module.StreamManager(object(), core, self.config, logging.getLogger('weekly-test'))
        manager._stream_is_playable = lambda *args: True
        return manager

    def test_allowed_day_starts_once_and_restart_preserves_manual_stop(self):
        entry = channel()
        core = Core(entry)
        manager = self.manager(core)
        manager.core_api_sync()
        self.assertEqual(core.commands, [(entry['id'], 'start')])
        entry['state'] = {'exec': 'finished', 'order': 'stop'}
        manager.core_api_sync()
        self.manager(core).core_api_sync()
        self.assertEqual(core.commands, [(entry['id'], 'start')])
        self.assertIn('weekly', manager.catalogue)
        self.assertNotIn('weekly', manager.database)

    def test_excluded_day_stops_once_then_respects_manual_start(self):
        entry = channel(days=('tue',), running=True)
        core = Core(entry)
        manager = self.manager(core)
        manager.core_api_sync()
        self.assertEqual(core.commands, [(entry['id'], 'stop')])
        self.assertNotIn('weekly', manager.database)
        entry['state'] = {'exec': 'running', 'order': 'start'}
        manager.core_api_sync()
        self.assertEqual(core.commands, [(entry['id'], 'stop')])
        self.assertIn('weekly', manager.database)

    def test_next_excluded_day_stops_reconnecting_input(self):
        entry = channel(running=True)
        core = Core(entry)
        manager = self.manager(core)
        manager.core_api_sync()
        entry['state']['exec'] = 'failed'
        Clock.current = datetime(2026, 11, 3, 0, 0, 8, tzinfo=timezone.utc)
        manager.core_api_sync()
        self.assertEqual(core.commands, [(entry['id'], 'stop')])
        self.assertEqual(manager.schedule_day, '2026-11-03')
        self.assertNotIn('weekly', manager.database)

    def test_consecutive_allowed_days_do_not_restart_running_input(self):
        entry = channel(days=('mon', 'tue'), running=True)
        core = Core(entry)
        manager = self.manager(core)
        manager.core_api_sync()
        Clock.current = datetime(2026, 11, 3, 0, 0, 8, tzinfo=timezone.utc)
        manager.core_api_sync()
        self.assertEqual(core.commands, [])
        self.assertEqual(manager._daily_state['decisions']['weekly'], '2026-11-03')

    def test_missing_days_and_never_do_not_control_inputs(self):
        core = Core(channel('legacy', days=None), channel('disabled', start='never'))
        manager = self.manager(core)
        manager.core_api_sync()
        Clock.current = datetime(2026, 11, 3, 0, 0, 8, tzinfo=timezone.utc)
        manager.core_api_sync()
        self.assertEqual(core.commands, [])
        self.assertIn('legacy', manager.catalogue)
        self.assertEqual(manager.database, {})
        self.assertEqual(manager._daily_state, {})

    def test_failed_command_is_not_repeated_by_poll_or_restart(self):
        core = Core(channel())
        core.fail_commands = True
        manager = self.manager(core)
        manager.core_api_sync()
        manager.core_api_sync()
        self.manager(core).core_api_sync()
        self.assertEqual(len(core.commands), 1)

    def test_persistence_failure_does_not_send_unrecorded_command(self):
        core = Core(channel())
        manager = self.manager(core)
        with patch.object(manager, '_save_daily_state', side_effect=OSError('read-only')):
            with self.assertRaises(OSError):
                manager.core_api_sync()
        self.assertEqual(core.commands, [])
        self.assertIsNone(manager.schedule_day)

    def test_midnight_and_daytime_programmes_wait_for_readiness_without_missing_start(self):
        for hour in (0, 18):
            with self.subTest(hour=hour):
                Clock.current = datetime(2026, 11, 2, hour, 0, 5, tzinfo=timezone.utc)
                entry = channel(name=f'show-{hour}', start=f'{hour:02}00', running=True, priority=10)
                fallback = channel(name=f'fallback-{hour}', days=None, start='now', running=True)
                core = Core(entry, fallback)
                manager = self.manager(core)
                playable = {fallback['reference']}
                manager._stream_is_playable = lambda key, url: key in playable
                manager.core_api_sync()
                self.assertEqual(manager.playhead['id'], fallback['reference'])
                playable.add(entry['reference'])
                Clock.current = Clock.current.replace(minute=1)
                manager.core_api_sync()
                self.assertEqual(manager.playhead['id'], entry['reference'])
                playable.remove(entry['reference'])
                manager.core_api_sync()
                self.assertEqual(manager.playhead['id'], fallback['reference'])

    def test_simultaneous_equal_priority_programmes_do_not_alternate_each_sync(self):
        core = Core(channel('a', running=True), channel('b', running=True))
        manager = self.manager(core)
        manager.core_api_sync()
        selected = manager.playhead['id']
        for _ in range(3):
            manager.core_api_sync()
            self.assertEqual(manager.playhead['id'], selected)

    def test_publication_process_cannot_take_over_ingest_daily_control(self):
        ingest = channel()
        publication = channel()
        publication['id'] = 'restreamer-ui:egress:weekly'
        core = Core(publication, ingest)
        manager = self.manager(core)
        manager.core_api_sync()
        self.assertEqual(core.commands, [(ingest['id'], 'start')])

    def test_publication_without_ingest_does_not_consume_daily_decision(self):
        publication = channel()
        publication['id'] = 'restreamer-ui:egress:weekly'
        core = Core(publication)
        manager = self.manager(core)
        manager.core_api_sync()
        self.assertEqual(core.commands, [])
        ingest = channel()
        core.channels[ingest['id']] = ingest
        manager.core_api_sync()
        self.assertEqual(core.commands, [(ingest['id'], 'start')])

    def test_announcement_date_is_separate_and_persists_without_losing_decisions(self):
        core = Core(channel())
        manager = self.manager(core)
        manager.core_api_sync()
        self.assertIsNone(manager.daily_announcement_date())
        manager.mark_daily_announced('2026-11-02')
        restored = self.manager(core)
        self.assertEqual(restored.daily_announcement_date(), '2026-11-02')
        restored.core_api_sync()
        self.assertEqual(len(core.commands), 1)


class HLSReadinessTest(unittest.TestCase):
    def make_manager(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        config = types.SimpleNamespace(schedule_state_path=str(Path(directory.name) / 'state.json'))
        return module.StreamManager(object(), object(), config, logging.getLogger('readiness-test'))

    def test_playlist_must_advance_and_latest_segment_must_be_accessible(self):
        manager = self.make_manager()
        sequence = [1]
        accessible = [True]

        class Response:
            def __init__(self, segment=False):
                self.segment = segment
                self.text = (f'#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:{sequence[0]}\n'
                             f'#EXTINF:4,\nsegment-{sequence[0]}.ts\n')

            def raise_for_status(self):
                if self.segment and not accessible[0]:
                    raise module.requests.RequestException('missing segment')

            def iter_content(self, chunk_size):
                return iter([b'x'])

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        def get(url, **kwargs):
            return Response(segment=url.endswith('.ts'))

        with patch.object(module.requests, 'get', side_effect=get), patch.object(module.time, 'monotonic') as clock:
            clock.return_value = 0
            self.assertFalse(manager._stream_is_playable('id', 'http://source/live.m3u8'))
            sequence[0] = 2
            clock.return_value = 15
            self.assertTrue(manager._stream_is_playable('id', 'http://source/live.m3u8'))
            accessible[0] = False
            self.assertFalse(manager._stream_is_playable('id', 'http://source/live.m3u8'))
            accessible[0] = True
            clock.return_value = 76
            self.assertFalse(manager._stream_is_playable('id', 'http://source/live.m3u8'))


if __name__ == '__main__':
    unittest.main()
