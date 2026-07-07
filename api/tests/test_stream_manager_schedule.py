# pyright: reportMissingImports=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportAttributeAccessIssue=false, reportArgumentType=false, reportAny=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportUnannotatedClassAttribute=false, reportUnknownLambdaType=false, reportUnknownArgumentType=false, reportUnusedCallResult=false

import importlib.util
import sys
import types
import unittest
from pathlib import Path


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
    core_api_module = types.ModuleType('services.core_api')

    class CoreAPIClient:
        pass

    core_api_module.CoreAPIClient = CoreAPIClient
    playhead_metadata_module = types.ModuleType('services.playhead_metadata')

    class PlayheadMetadataPoller:
        def __init__(self, client, logger):
            self.client = client
            self.logger = logger

        def poll(self, playhead, timeout):
            _ = timeout
            return playhead

    playhead_metadata_module.PlayheadMetadataPoller = PlayheadMetadataPoller
    sys.modules['services'] = services_module
    sys.modules['services.core_api'] = core_api_module
    sys.modules['services.playhead_metadata'] = playhead_metadata_module

    module_path = Path(__file__).resolve().parents[1] / 'services' / 'stream_manager.py'
    spec = importlib.util.spec_from_file_location('stream_manager_under_test', module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


stream_manager_module = load_stream_manager_module()


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


class StreamManagerScheduleTest(unittest.TestCase):
    def make_manager(self):
        scheduler = DummyScheduler()
        logger = DummyLogger()
        manager = stream_manager_module.StreamManager(scheduler, object(), object(), logger)
        return manager, scheduler, logger

    def test_parse_military_time_accepts_supported_formats(self):
        self.assertEqual(stream_manager_module.parse_military_time('1745'), (17, 45))
        self.assertEqual(stream_manager_module.parse_military_time('14'), (14, 0))
        self.assertEqual(stream_manager_module.parse_military_time('0030'), (0, 30))

    def test_parse_military_time_rejects_disabled_and_invalid_values(self):
        for value in ['never', '2500', '2360', '2400', '', None]:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    stream_manager_module.parse_military_time(value)

    def test_disabled_stream_is_skipped_without_scheduling(self):
        manager, scheduler, _ = self.make_manager()

        manager.process_running_channel('id-1', 'Disabled', "{'start_at': 'never'}", 'https://example.test/live.m3u8')

        self.assertEqual(scheduler.jobs, [])
        self.assertEqual(manager.database, {})

    def test_invalid_start_time_is_skipped_without_scheduling(self):
        manager, scheduler, logger = self.make_manager()

        manager.process_running_channel('id-1', 'Bad Time', "{'start_at': '2500'}", 'https://example.test/live.m3u8')

        self.assertEqual(scheduler.jobs, [])
        self.assertEqual(manager.database, {})
        self.assertTrue(any('invalid schedule time' in message for message in logger.warning_messages))

    def test_stream_access_probe_passes_timeout_to_requests(self):
        manager, _, _ = self.make_manager()
        calls = []

        class Response:
            status_code = 200

        original_get = stream_manager_module.requests.get
        original_sleep = stream_manager_module.time.sleep
        try:
            stream_manager_module.time.sleep = lambda _seconds: None

            def fake_get(url, timeout):
                calls.append((url, timeout))
                return Response()

            stream_manager_module.requests.get = fake_get

            self.assertTrue(manager._wait_for_stream_access('https://example.test/live.m3u8', 'Live'))
        finally:
            stream_manager_module.requests.get = original_get
            stream_manager_module.time.sleep = original_sleep

        self.assertEqual(calls, [('https://example.test/live.m3u8', stream_manager_module.STREAM_ACCESS_TIMEOUT)])


if __name__ == '__main__':
    unittest.main()
