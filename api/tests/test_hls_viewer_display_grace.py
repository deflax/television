# pyright: reportImplicitRelativeImport=false

import importlib.util
import asyncio
import contextlib
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
    HLSViewerSession,
    WebRouteState,
    apply_hls_viewer_display_grace,
    hls_viewer_display_key,
    update_hls_viewer_display_state,
)
from web.timecode_manager import TimecodeManager
from web.visitor_tracker import VisitorTracker


@contextlib.contextmanager
def patched_attr(module, name: str, value):
    original = getattr(module, name)
    setattr(module, name, value)
    try:
        yield
    finally:
        setattr(module, name, original)


class FakeDiscordBotManager:
    def __init__(self):
        self.connects: list[tuple[str, int]] = []
        self.changes = 0
        self.hls_viewer_ips: set[str] = set()

    def log_visitor_connect(self, ip: str, count: int) -> bool:
        self.connects.append((ip, count))
        return True

    def log_visitor_change(self) -> bool:
        self.changes += 1
        return True


def load_routes_module():
    api_routes_module = types.ModuleType('web.api_routes')

    def noop_api_routes(*args, **kwargs):
        return None
    setattr(api_routes_module, 'register_api_routes', noop_api_routes)
    frontend_routes_module = types.ModuleType('web.frontend_routes')

    def noop_frontend_routes(*args, **kwargs):
        return None
    setattr(frontend_routes_module, 'register_frontend_routes', noop_frontend_routes)
    sys.modules['web.api_routes'] = api_routes_module
    sys.modules['web.frontend_routes'] = frontend_routes_module

    spec = importlib.util.spec_from_file_location(
        'routes_under_test',
        api_dir / 'web' / 'routes.py',
    )
    if spec is None or spec.loader is None:
        raise RuntimeError('could not load routes.py')

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sys.modules.pop('utils.obfuscation', None)
    sys.modules.pop('utils', None)
    return module


def load_discord_bot_manager_module():
    integrations_dir = api_dir / 'integrations'
    integrations_module = types.ModuleType('integrations')
    integrations_module.__path__ = [str(integrations_dir)]
    _ = sys.modules.setdefault('integrations', integrations_module)

    discord_module = types.ModuleType('discord')
    setattr(discord_module, 'Embed', object)
    setattr(discord_module, 'NotFound', Exception)
    setattr(discord_module, 'Forbidden', Exception)
    setattr(discord_module, 'HTTPException', Exception)
    sys.modules.setdefault('discord', discord_module)

    commands_module = types.ModuleType('discord.ext.commands')

    class Bot:
        pass

    class CheckFailure(Exception):
        pass

    def check(predicate):
        def decorator(func):
            return func
        return decorator
    setattr(commands_module, 'Bot', Bot)
    setattr(commands_module, 'CheckFailure', CheckFailure)
    setattr(commands_module, 'check', check)

    scheduler_module = types.ModuleType('apscheduler.schedulers.asyncio')

    class AsyncIOScheduler:
        pass
    setattr(scheduler_module, 'AsyncIOScheduler', AsyncIOScheduler)
    apscheduler_module = types.ModuleType('apscheduler')
    schedulers_module = types.ModuleType('apscheduler.schedulers')
    asyncio_module = types.ModuleType('apscheduler.schedulers.asyncio')
    setattr(asyncio_module, 'AsyncIOScheduler', AsyncIOScheduler)
    sys.modules.setdefault('apscheduler', apscheduler_module)
    sys.modules.setdefault('apscheduler.schedulers', schedulers_module)
    sys.modules.setdefault('apscheduler.schedulers.asyncio', asyncio_module)

    ext_module = types.ModuleType('discord.ext')
    setattr(ext_module, 'commands', commands_module)
    setattr(discord_module, 'ext', ext_module)
    _ = sys.modules.setdefault('discord.ext', ext_module)
    _ = sys.modules.setdefault('discord.ext.commands', commands_module)

    spec = importlib.util.spec_from_file_location(
        'discord_bot_manager_under_test',
        integrations_dir / 'discord_bot_manager.py',
    )
    if spec is None or spec.loader is None:
        raise RuntimeError('could not load discord_bot_manager.py')

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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

    def test_ipv6_addresses_in_same_prefix_share_display_key(self):
        state = make_state()

        displayed = apply_hls_viewer_display_grace(
            state,
            {
                '2a01:5a8:302:59c0:f0f1:21e5:fbc3:7799',
                '2a01:5a8:302:59c0:abcd:1111:2222:3333',
            },
            100.0,
        )

        self.assertEqual(displayed, {'2a01:5a8:302:59c0::/64'})

    def test_ipv6_addresses_in_different_prefixes_count_separately(self):
        state = make_state()

        displayed = apply_hls_viewer_display_grace(
            state,
            {
                '2a01:5a8:302:59c0:f0f1:21e5:fbc3:7799',
                '2a01:5a8:302:59c1:f0f1:21e5:fbc3:7799',
            },
            100.0,
        )

        self.assertEqual(
            displayed,
            {
                '2a01:5a8:302:59c0::/64',
                '2a01:5a8:302:59c1::/64',
            },
        )

    def test_ipv6_rotation_refreshes_existing_display_key(self):
        state = make_state()

        _ = apply_hls_viewer_display_grace(
            state,
            {'2a01:5a8:302:59c0:f0f1:21e5:fbc3:7799'},
            100.0,
        )
        displayed = apply_hls_viewer_display_grace(
            state,
            {'2a01:5a8:302:59c0:abcd:1111:2222:3333'},
            341.0,
        )

        self.assertEqual(displayed, {'2a01:5a8:302:59c0::/64'})

    def test_invalid_viewer_key_is_preserved(self):
        self.assertEqual(hls_viewer_display_key('unknown-viewer'), 'unknown-viewer')

    def test_dropped_viewer_reports_final_connection_duration(self):
        state = make_state()

        first_drop = update_hls_viewer_display_state(
            state,
            {'8.8.8.8': HLSViewerSession(connected_seconds=20.0, last_seen=120.0)},
            120.0,
        )
        held_drop = update_hls_viewer_display_state(state, {}, 350.0)
        final_drop = update_hls_viewer_display_state(state, {}, 361.0)

        self.assertEqual(first_drop.displayed_ips, {'8.8.8.8'})
        self.assertEqual(first_drop.disconnected_durations, {})
        self.assertEqual(held_drop.displayed_ips, {'8.8.8.8'})
        self.assertEqual(held_drop.disconnected_durations, {})
        self.assertEqual(final_drop.displayed_ips, set())
        self.assertEqual(final_drop.disconnected_durations, {'8.8.8.8': 20.0})

    def test_hls_viewers_route_rejects_malformed_connected_seconds(self):
        quart_module = types.ModuleType('quart')
        setattr(quart_module, 'request', None)
        sys.modules.setdefault('quart', quart_module)
        helpers_module = types.ModuleType('web.helpers')
        setattr(helpers_module, 'get_client_address', lambda request: '8.8.8.8')
        sys.modules.setdefault('web.helpers', helpers_module)

        from web import sse_routes as module

        state = make_state()
        captured_handler = None

        class FakeLogger:
            def info(self, message: str) -> None:
                pass

        class FakeLoggers:
            sse = FakeLogger()

        class FakeApp:
            def route(self, path: str, methods: list[str]):
                def decorator(func):
                    nonlocal captured_handler
                    if path == '/hls-viewers':
                        captured_handler = func
                    return func
                return decorator

            def before_serving(self, func):
                return func

        class FakeRequest:
            async def get_json(self):
                return {'count': 1, 'viewers': {'8.8.8.8': {'connected_seconds': 'bad'}}}

        register_sse_routes = getattr(module, 'register_sse_routes')
        register_sse_routes(FakeApp(), None, FakeLoggers(), None, state)
        if captured_handler is None:
            raise AssertionError('hls-viewers handler was not captured')

        with patched_attr(module, 'request', FakeRequest()):
            response = asyncio.run(captured_handler())

        self.assertEqual(response, ('Bad request', 400))


class DiscordHLSConnectMessageTest(unittest.TestCase):
    def test_sse_connect_no_longer_logs_satellite_connect(self):
        routes = load_routes_module()
        discord = FakeDiscordBotManager()
        captured_tracker: VisitorTracker | None = None

        class FakeApp:
            pass

        def fake_register_frontend_routes(app, config, loggers, state):
            pass

        def fake_register_api_routes(app, stream_manager, loggers, discord_bot_manager, state):
            nonlocal captured_tracker
            captured_tracker = state.visitor_tracker

        original_frontend = getattr(routes, 'register_frontend_routes')
        original_api = getattr(routes, 'register_api_routes')
        setattr(routes, 'register_frontend_routes', fake_register_frontend_routes)
        setattr(routes, 'register_api_routes', fake_register_api_routes)
        try:
            register_routes = getattr(routes, 'register_routes')
            register_routes(FakeApp(), None, None, None, discord)
            if captured_tracker is None:
                raise AssertionError('visitor tracker was not captured')
            captured_tracker.connect('8.8.8.8')
        finally:
            setattr(routes, 'register_frontend_routes', original_frontend)
            setattr(routes, 'register_api_routes', original_api)

        self.assertEqual(discord.connects, [])
        self.assertEqual(discord.changes, 1)

    def test_hls_first_appearance_logs_satellite_connect_once(self):
        module = load_discord_bot_manager_module()

        class Manager:
            update_hls_viewers = module.DiscordBotManager.update_hls_viewers

            def __init__(self):
                self.hls_viewer_ips: set[str] = set()
                self.connects: list[tuple[str, int]] = []
                self.embed_updates = 0

            def log_visitor_connect(self, ip: str, count: int) -> bool:
                self.connects.append((ip, count))
                return True

            def _schedule_debounced_visitor_update(self) -> bool:
                self.embed_updates += 1
                return True

        manager = Manager()
        manager.update_hls_viewers({'2a01:5a8:302:59c0::/64'}, 1)
        manager.update_hls_viewers({'2a01:5a8:302:59c0::/64'}, 1)
        manager.update_hls_viewers({'2a01:5a8:302:59c0::/64', '1.1.1.1'}, 2)

        self.assertEqual(manager.connects, [('2a01:5a8:302:59c0::/64', 1), ('1.1.1.1', 2)])
        self.assertEqual(manager.embed_updates, 2)

    def test_hls_disappearance_logs_disconnect_duration_once(self):
        module = load_discord_bot_manager_module()

        class Manager:
            update_hls_viewers = module.DiscordBotManager.update_hls_viewers

            def __init__(self):
                self.hls_viewer_ips: set[str] = {'8.8.8.8'}
                self.connects: list[tuple[str, int]] = []
                self.disconnects: list[tuple[str, float]] = []
                self.embed_updates = 0

            def log_visitor_connect(self, ip: str, count: int) -> bool:
                self.connects.append((ip, count))
                return True

            def log_visitor_disconnect(self, ip: str, connected_seconds: float) -> bool:
                self.disconnects.append((ip, connected_seconds))
                return True

            def _schedule_debounced_visitor_update(self) -> bool:
                self.embed_updates += 1
                return True

        manager = Manager()
        manager.update_hls_viewers(set(), 0, {'8.8.8.8': 261.0})
        manager.update_hls_viewers(set(), 0, {'8.8.8.8': 999.0})

        self.assertEqual(manager.connects, [])
        self.assertEqual(manager.disconnects, [('8.8.8.8', 261.0)])
        self.assertEqual(manager.embed_updates, 1)

    def test_disconnect_edits_original_connect_message(self):
        module = load_discord_bot_manager_module()

        class Message:
            def __init__(self, content: str):
                self.content = content

            async def edit(self, content: str) -> None:
                self.content = content

        class Channel:
            def __init__(self):
                self.message: Message | None = None

            async def send(self, content: str) -> Message:
                self.message = Message(content)
                return self.message

        class Bot:
            def __init__(self, channel: Channel):
                self.channel = channel

            def get_channel(self, channel_id: int) -> Channel:
                return self.channel

        class Manager:
            _send_visitor_connect_log_async = module.DiscordBotManager._send_visitor_connect_log_async
            _edit_visitor_disconnect_log_async = module.DiscordBotManager._edit_visitor_disconnect_log_async

            def __init__(self):
                self.live_channel_id = 1
                self.channel = Channel()
                self.bot = Bot(self.channel)
                self._visitor_connect_messages = {}

        async def run_scenario() -> Message:
            manager = Manager()
            await manager._send_visitor_connect_log_async('8.8.8.8', 1)
            if manager.channel.message is None:
                raise AssertionError('connect message was not sent')
            await manager._edit_visitor_disconnect_log_async('8.8.8.8', 65.0)
            return manager.channel.message

        message = asyncio.run(run_scenario())

        self.assertIn('disconnected after 1m 5s', message.content)


if __name__ == '__main__':
    _ = unittest.main()
