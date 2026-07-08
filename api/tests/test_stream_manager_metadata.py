# pyright: reportMissingImports=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportAttributeAccessIssue=false, reportArgumentType=false, reportAny=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportUnannotatedClassAttribute=false, reportUnknownLambdaType=false, reportUnknownArgumentType=false, reportUnusedCallResult=false, reportUnusedParameter=false

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from typing import Literal, TypeAlias


SourceShape: TypeAlias = Literal[
    'config.input[0].address',
    'input.address',
    'inputs[0].address',
]


def load_stream_manager_module():
    requests_module = types.ModuleType('requests')

    class RequestException(Exception):
        pass

    requests_module.RequestException = RequestException
    requests_module.get = lambda *args, **kwargs: None
    sys.modules['requests'] = requests_module

    apscheduler_module = types.ModuleType('apscheduler')
    schedulers_module = types.ModuleType('apscheduler.schedulers')
    background_module = types.ModuleType('apscheduler.schedulers.background')

    class BackgroundScheduler:
        pass

    background_module.BackgroundScheduler = BackgroundScheduler
    sys.modules['apscheduler'] = apscheduler_module
    sys.modules['apscheduler.schedulers'] = schedulers_module
    sys.modules['apscheduler.schedulers.background'] = background_module

    services_module = types.ModuleType('services')
    services_module.__path__ = []

    core_api_module = types.ModuleType('services.core_api')

    class CoreAPIClient:
        pass

    core_api_module.CoreAPIClient = CoreAPIClient

    icy_metadata_module = types.ModuleType('services.icy_metadata')

    def read_icy_stream_title(source_url: str, timeout: float) -> str | None:
        return None

    def read_icy_metadata_result(source_url: str, timeout: float):
        _ = source_url
        _ = timeout
        return types.SimpleNamespace(title=None, reason='test-no-title')

    icy_metadata_module.read_icy_stream_title = read_icy_stream_title
    icy_metadata_module.read_icy_metadata_result = read_icy_metadata_result

    services_module.core_api = core_api_module
    services_module.icy_metadata = icy_metadata_module
    sys.modules['services'] = services_module
    sys.modules['services.core_api'] = core_api_module
    sys.modules['services.icy_metadata'] = icy_metadata_module

    api_dir = Path(__file__).resolve().parents[1]
    playhead_metadata_path = api_dir / 'services' / 'playhead_metadata.py'
    playhead_metadata_spec = importlib.util.spec_from_file_location(
        'services.playhead_metadata',
        playhead_metadata_path,
    )
    assert playhead_metadata_spec is not None and playhead_metadata_spec.loader is not None
    playhead_metadata_module = importlib.util.module_from_spec(playhead_metadata_spec)
    sys.modules['services.playhead_metadata'] = playhead_metadata_module
    services_module.playhead_metadata = playhead_metadata_module
    playhead_metadata_spec.loader.exec_module(playhead_metadata_module)

    module_path = api_dir / 'services' / 'stream_manager.py'
    spec = importlib.util.spec_from_file_location('stream_manager_under_test', module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module, icy_metadata_module, playhead_metadata_module


stream_manager_module, icy_metadata_module, playhead_metadata_module = load_stream_manager_module()


class DummyScheduler:
    def __init__(self):
        self.jobs = []

    def add_job(self, **kwargs):
        self.jobs.append(kwargs)


class DummyLogger:
    def __init__(self):
        self.debug_messages = []
        self.warning_messages = []
        self.info_messages = []
        self.error_messages = []

    def debug(self, message):
        self.debug_messages.append(message)

    def warning(self, message):
        self.warning_messages.append(message)

    def info(self, message):
        self.info_messages.append(message)

    def error(self, message):
        self.error_messages.append(message)


class DummyCoreClient:
    def __init__(self, processes, process_details):
        self._processes = processes
        self._process_details = process_details

    def v3_process_get_list(self):
        return self._processes

    def v3_process_get(self, id):
        return self._process_details[id]


def build_process_fixture(process_id: str, reference: str, shape: SourceShape, source_url: str, state: str = 'running'):
    detail = {
        'id': process_id,
        'reference': reference,
        'state': {'exec': state},
    }
    match shape:
        case 'config.input[0].address':
            detail['config'] = {'input': [{'address': source_url}]}
        case 'input.address':
            detail['input'] = {'address': source_url}
        case 'inputs[0].address':
            detail['inputs'] = [{'address': source_url}]
    return detail


class StreamManagerMetadataTest(unittest.TestCase):
    def make_manager(self, processes, process_details):
        scheduler = DummyScheduler()
        logger = DummyLogger()
        config = types.SimpleNamespace(
            core_hostname='core.example.test',
            icy_timeout=3.5,
            metadata_timeout=3.5,
        )
        client = DummyCoreClient(processes, process_details)
        manager = stream_manager_module.StreamManager(scheduler, client, config, logger)
        return manager, scheduler, logger

    def test_poll_current_metadata_uses_current_process_source_url_and_ignores_other_processes(self):
        current_process_id = 'channel-current'
        expected_source_url = 'https://example.test/current/live.m3u8'
        other_source_url = 'https://example.test/other/live.m3u8'

        for shape in [
            'config.input[0].address',
            'input.address',
            'inputs[0].address',
        ]:
            with self.subTest(shape=shape):
                processes = [
                    {'id': 'channel-other', 'reference': 'channel-other'},
                    {'id': current_process_id, 'reference': current_process_id},
                ]
                process_details = {
                    'channel-other': build_process_fixture(
                        'channel-other',
                        'channel-other',
                        shape,
                        other_source_url,
                    ),
                    current_process_id: build_process_fixture(
                        current_process_id,
                        current_process_id,
                        shape,
                        expected_source_url,
                    ),
                }
                manager, _, logger = self.make_manager(processes, process_details)
                manager.playhead = {
                    'id': current_process_id,
                    'name': 'Current Channel',
                    'prio': 5,
                    'head': 'https://example.test/stale/live.m3u8',
                }

                calls = []

                def fake_read_icy_metadata_result(source_url: str, timeout: float):
                    calls.append(source_url)
                    self.assertEqual(source_url, expected_source_url)
                    _ = timeout
                    return types.SimpleNamespace(title='Artist - Track', reason='ok')

                original_icy_reader = icy_metadata_module.read_icy_metadata_result
                original_playhead_reader = playhead_metadata_module.read_icy_metadata_result
                try:
                    icy_metadata_module.read_icy_metadata_result = fake_read_icy_metadata_result
                    playhead_metadata_module.read_icy_metadata_result = fake_read_icy_metadata_result

                    manager.poll_current_metadata()
                finally:
                    icy_metadata_module.read_icy_metadata_result = original_icy_reader
                    playhead_metadata_module.read_icy_metadata_result = original_playhead_reader

                self.assertEqual(calls, [expected_source_url])
                self.assertTrue(
                    any('Polling ICY metadata for Current Channel' in message for message in logger.info_messages)
                )
                self.assertTrue(
                    any('Updated ICY metadata for Current Channel: Artist - Track' in message for message in logger.info_messages)
                )
                self.assertEqual(
                    manager.playhead,
                    {
                        'id': current_process_id,
                        'name': 'Current Channel',
                        'prio': 5,
                        'head': 'https://example.test/stale/live.m3u8',
                        'metadata': {'stream_title': 'Artist - Track'},
                    },
                )

    def test_poll_current_metadata_logs_when_current_source_url_is_missing(self):
        processes = [{'id': 'channel-current', 'reference': 'channel-current'}]
        process_details = {
            'channel-current': {
                'id': 'channel-current',
                'reference': 'channel-current',
                'state': {'exec': 'running'},
                'config': {'input': []},
            },
        }
        manager, _, logger = self.make_manager(processes, process_details)
        manager.playhead = {
            'id': 'channel-current',
            'name': 'Current Channel',
            'prio': 5,
            'head': 'https://example.test/current/live.m3u8',
        }

        manager.poll_current_metadata()

        self.assertTrue(
            any('No metadata source URL found for Current Channel' in message for message in logger.info_messages)
        )
        self.assertNotIn('metadata', manager.playhead)

    def test_poll_current_metadata_logs_parser_reason_when_title_is_missing(self):
        current_process_id = 'channel-current'
        source_url = 'https://example.test/current/live.m3u8'
        processes = [{'id': current_process_id, 'reference': current_process_id}]
        process_details = {
            current_process_id: build_process_fixture(
                current_process_id,
                current_process_id,
                'config.input[0].address',
                source_url,
            ),
        }
        manager, _, logger = self.make_manager(processes, process_details)
        manager.playhead = {
            'id': current_process_id,
            'name': 'Current Channel',
            'prio': 5,
            'head': 'https://example.test/stale/live.m3u8',
            'metadata': {'stream_title': 'Stale Track'},
        }

        def fake_read_icy_metadata_result(source_url_arg: str, timeout: float):
            self.assertEqual(source_url_arg, source_url)
            _ = timeout
            return types.SimpleNamespace(title=None, reason='missing-or-invalid-icy-metaint')

        original_icy_reader = icy_metadata_module.read_icy_metadata_result
        original_playhead_reader = playhead_metadata_module.read_icy_metadata_result
        try:
            icy_metadata_module.read_icy_metadata_result = fake_read_icy_metadata_result
            playhead_metadata_module.read_icy_metadata_result = fake_read_icy_metadata_result

            manager.poll_current_metadata()
        finally:
            icy_metadata_module.read_icy_metadata_result = original_icy_reader
            playhead_metadata_module.read_icy_metadata_result = original_playhead_reader

        self.assertTrue(
            any(
                'No ICY metadata title found for Current Channel: missing-or-invalid-icy-metaint' in message
                for message in logger.info_messages
            )
        )
        self.assertNotIn('metadata', manager.playhead)

    def test_update_playhead_removes_stale_metadata_when_switching_channels(self):
        manager, _, _ = self.make_manager([], {})
        manager.playhead = {
            'id': 'channel-current',
            'name': 'Current Channel',
            'prio': 5,
            'head': 'https://example.test/current/live.m3u8',
            'metadata': {'stream_title': 'Artist - Track'},
        }

        manager.update_playhead(
            'channel-next',
            'Next Channel',
            2,
            'https://example.test/next/live.m3u8',
        )

        self.assertEqual(
            manager.playhead,
            {
                'id': 'channel-next',
                'name': 'Next Channel',
                'prio': 2,
                'head': 'https://example.test/next/live.m3u8',
            },
        )
        self.assertNotIn('metadata', manager.playhead)


if __name__ == '__main__':
    unittest.main()
