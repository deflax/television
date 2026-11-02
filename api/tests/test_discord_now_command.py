import asyncio
import importlib.util
import sys
import types
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock, patch
from pathlib import Path


api_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(api_dir))


def load_discord_bot_manager_module():
    integrations_dir = api_dir / 'integrations'
    services_module = types.ModuleType('services')
    services_module.__path__ = [str(api_dir / 'services')]

    discord_module = types.ModuleType('discord')
    setattr(discord_module, 'Embed', object)
    setattr(discord_module, 'NotFound', Exception)
    setattr(discord_module, 'Forbidden', Exception)
    setattr(discord_module, 'HTTPException', Exception)
    discord_module.AllowedMentions = types.SimpleNamespace(
        none=lambda: types.SimpleNamespace(everyone=False, users=False, roles=False, replied_user=False)
    )

    commands_module = types.ModuleType('discord.ext.commands')

    class Bot:
        pass

    class CheckFailure(Exception):
        pass

    def check(predicate):
        _ = predicate

        def decorator(func):
            return func

        return decorator

    setattr(commands_module, 'Bot', Bot)
    setattr(commands_module, 'CheckFailure', CheckFailure)
    setattr(commands_module, 'check', check)

    ext_module = types.ModuleType('discord.ext')
    setattr(ext_module, 'commands', commands_module)
    setattr(discord_module, 'ext', ext_module)

    scheduler_module = types.ModuleType('apscheduler.schedulers.asyncio')

    class AsyncIOScheduler:
        pass

    setattr(scheduler_module, 'AsyncIOScheduler', AsyncIOScheduler)

    spec = importlib.util.spec_from_file_location(
        'discord_bot_manager_under_test',
        integrations_dir / 'discord_bot_manager.py',
    )
    if spec is None or spec.loader is None:
        raise RuntimeError('could not load discord_bot_manager.py')

    module = importlib.util.module_from_spec(spec)
    dependencies = {
        'discord': discord_module,
        'discord.ext': ext_module,
        'discord.ext.commands': commands_module,
        'apscheduler': types.ModuleType('apscheduler'),
        'apscheduler.schedulers': types.ModuleType('apscheduler.schedulers'),
        'apscheduler.schedulers.asyncio': scheduler_module,
        'services': services_module,
    }
    with patch.dict(sys.modules, dependencies):
        spec.loader.exec_module(module)
    return module


class DiscordNowCommandTest(unittest.TestCase):
    def make_command_manager(self, playhead=None):
        module = load_discord_bot_manager_module()
        commands = {}

        class Bot:
            def command(self, name: str, help: str):
                _ = help

                def decorator(func):
                    func.error = lambda handler: handler
                    commands[name] = func
                    return func

                return decorator

        class Channel:
            def __init__(self):
                self.embeds = []

            async def send(self, embed):
                self.embeds.append(embed)

        class Context:
            def __init__(self):
                self.channel = Channel()

        class Manager:
            _setup_bot_commands = module.DiscordBotManager._setup_bot_commands
            _require_any_role = module.DiscordBotManager._require_any_role
            _escape_discord_markdown = staticmethod(module.DiscordBotManager._escape_discord_markdown)
            COLOR_SUCCESS = module.DiscordBotManager.COLOR_SUCCESS

            def __init__(self):
                self.bot = Bot()
                self.boss_role_name = 'bosmang'
                self.worshipper_role_name = 'worshipper'
                self.database = {}
                self.stream_manager = types.SimpleNamespace(database={})

            async def query_playhead(self):
                return playhead or {}

            def _make_embed(self, **kwargs):
                return types.SimpleNamespace(**kwargs)

        manager = Manager()
        manager._setup_bot_commands()
        return commands, Context()

    def test_now_command_prefers_icy_stream_title_when_available(self):
        commands, ctx = self.make_command_manager(
            {
                'name': 'Current Channel',
                'metadata': {'stream_title': 'Artist - Track'},
            }
        )

        asyncio.run(commands['now'](ctx))

        self.assertEqual(ctx.channel.embeds[-1].description, '**Artist - Track**')

    def test_now_command_falls_back_to_channel_name_without_icy_title(self):
        commands, ctx = self.make_command_manager({'name': 'Current Channel', 'metadata': {}})

        asyncio.run(commands['now'](ctx))

        self.assertEqual(ctx.channel.embeds[-1].description, '**Current Channel**')

    def test_now_command_escapes_markdown_in_icy_title(self):
        commands, ctx = self.make_command_manager(
            {
                'name': 'Current Channel',
                'metadata': {'stream_title': 'artist_name - track_title'},
            }
        )

        asyncio.run(commands['now'](ctx))

        self.assertEqual(ctx.channel.embeds[-1].description, '**artist\\_name - track\\_title**')

    def test_random_command_is_not_registered(self):
        commands, _ = self.make_command_manager({'name': 'Current Channel'})

        self.assertNotIn('rnd', commands)


class DiscordScheduleTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.module = load_discord_bot_manager_module()
        self.day = datetime(2026, 11, 2, 12, tzinfo=timezone.utc).date()
        fixed_now = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed_now if tz is not None else fixed_now.replace(tzinfo=None)

        self.clock_patch = patch.object(self.module, 'datetime', Clock)
        self.clock_patch.start()
        self.addCleanup(self.clock_patch.stop)
        self.channel = types.SimpleNamespace(send=AsyncMock())
        self.delivered = None
        self.stream_manager = types.SimpleNamespace(
            database={},
            catalogue={
                'offline': {
                    'name': 'Offline *film*',
                    'start_at': '18',
                    'details': 'A [description](https://example.net) @everyone',
                    'prio': 1,
                    'days': ['mon'],
                },
                'tomorrow': {
                    'name': 'Tuesday only',
                    'start_at': '19',
                    'details': '',
                    'prio': 1,
                    'days': ['tue'],
                },
                'daily': {
                    'name': 'Daily show',
                    'start_at': '20',
                    'details': 'Every day',
                    'prio': 1,
                },
                'live': {'name': 'Live only', 'start_at': 'now', 'details': '', 'prio': 1},
                'never': {'name': 'Never shown', 'start_at': 'never', 'details': '', 'prio': 1},
            },
            schedule_day=self.day.isoformat(),
            daily_announcement_date=lambda: self.delivered,
            mark_daily_announced=Mock(side_effect=self.mark_delivered),
        )
        self.manager = self.make_manager()

    def mark_delivered(self, day):
        self.delivered = day

    def make_manager(self):
        manager = self.module.DiscordBotManager.__new__(self.module.DiscordBotManager)
        manager.stream_manager = self.stream_manager
        manager.bot = types.SimpleNamespace(
            is_ready=Mock(return_value=True),
            get_channel=Mock(return_value=self.channel),
            user='test bot',
        )
        manager.logger = Mock()
        manager.scheduler = types.SimpleNamespace(
            get_job=Mock(return_value=None), remove_job=Mock(),
        )
        manager.live_channel_id = 123
        manager.scheduler_hostname = 'watch.example.com'
        manager._daily_announcement_lock = asyncio.Lock()
        manager._channel_messages = {}
        manager._make_embed = lambda **kwargs: types.SimpleNamespace(**kwargs)
        return manager

    async def test_empty_runtime_still_posts_and_restart_uses_persisted_date(self):
        await self.manager.query_database()
        self.assertEqual(self.delivered, '2026-11-02')
        self.assertEqual(self.channel.send.await_count, 1)
        await self.manager.query_database()
        restarted = self.make_manager()
        await restarted.query_database()
        self.assertEqual(self.channel.send.await_count, 1)
        self.assertEqual(self.manager._channel_messages, {})

    async def test_delivery_failure_retries_without_marking_success(self):
        self.channel.send.side_effect = RuntimeError('Discord unavailable')
        await self.manager.query_database()
        self.assertIsNone(self.delivered)
        self.stream_manager.mark_daily_announced.assert_not_called()
        self.channel.send.side_effect = None
        await self.manager.query_database()
        self.assertEqual(self.delivered, '2026-11-02')
        self.assertEqual(self.channel.send.await_count, 2)

    async def test_disconnected_or_missing_channel_retries_later(self):
        self.manager.bot.is_ready.return_value = False
        await self.manager.query_database()
        self.channel.send.assert_not_awaited()
        self.manager.bot.is_ready.return_value = True
        self.manager.bot.get_channel.return_value = None
        await self.manager.query_database()
        self.stream_manager.mark_daily_announced.assert_not_called()
        self.manager.bot.get_channel.return_value = self.channel
        await self.manager.query_database()
        self.assertEqual(self.delivered, '2026-11-02')

    async def test_unsynced_catalogue_waits_and_never_posts_backlog(self):
        self.delivered = '2026-10-30'
        self.stream_manager.schedule_day = '2026-11-01'
        await self.manager.query_database()
        self.channel.send.assert_not_awaited()
        self.stream_manager.schedule_day = '2026-11-02'
        await self.manager.query_database()
        self.assertEqual(self.channel.send.await_count, 1)
        self.assertIn('2026-11-02', self.channel.send.call_args.kwargs['embed'].title)

    async def test_today_view_uses_catalogue_and_is_shared_with_epg(self):
        commands = {}

        def command(name, help):
            def decorate(func):
                func.error = lambda handler: handler
                commands[name] = func
                return func
            return decorate

        self.manager.bot.command = command
        self.manager.boss_role_name = 'bosmang'
        self.manager.worshipper_role_name = 'worshipper'
        self.manager._setup_bot_commands()
        await self.manager.query_database()
        automatic = self.channel.send.call_args.kwargs['embed']
        command_channel = types.SimpleNamespace(send=AsyncMock())
        await commands['epg'](types.SimpleNamespace(channel=command_channel))
        manual = command_channel.send.call_args.kwargs['embed']
        self.assertEqual(vars(manual), vars(automatic))
        self.assertIn('2026-11-02 UTC', manual.title)
        self.assertIn('18:00–20:00 UTC', manual.description)
        self.assertIn('Offline \\*film\\*', manual.description)
        self.assertIn('\\[description\\]\\(https://example.net\\)', manual.description)
        self.assertIn('Daily show', manual.description)
        self.assertNotIn('Tuesday only', manual.description)
        self.assertNotIn('Live only', manual.description)
        self.assertNotIn('Never shown', manual.description)
        self.assertEqual(manual.fields[0]['value'], 'https://watch.example.com')
        self.assertIn('not guaranteed', manual.footer)
        mentions = command_channel.send.call_args.kwargs['allowed_mentions']
        self.assertFalse(mentions.everyone)
        self.assertFalse(mentions.users)
        self.assertFalse(mentions.roles)

    async def test_today_view_includes_only_current_runtime_live_entries(self):
        self.stream_manager.database = {
            'live': self.stream_manager.catalogue['live'],
            'wrong-day': {
                'name': 'Wrong day live',
                'start_at': 'now',
                'days': ['tue'],
                'prio': 1,
                'details': '',
            },
        }
        await self.manager._send_daily_schedule(
            self.channel, self.module.datetime.now(timezone.utc)
        )
        description = self.channel.send.call_args.kwargs['embed'].description
        self.assertIn('Live now (as of 12:00 UTC) — Live only', description)
        self.assertNotIn('Wrong day live', description)

    async def test_empty_day_is_explicit(self):
        self.stream_manager.catalogue = {}
        await self.manager.query_database()
        embed = self.channel.send.call_args.kwargs['embed']
        self.assertIn('No streams scheduled for this UTC date.', embed.description)
        self.assertEqual(self.delivered, '2026-11-02')

    async def test_large_metadata_splits_without_loss_and_marks_after_all_pages(self):
        details = 'Long *description* ' * 1200
        self.stream_manager.catalogue['offline']['details'] = details
        expected = self.manager._schedule_embeds(self.module.datetime.now(timezone.utc))
        self.assertGreater(len(expected), 1)

        async def send(**kwargs):
            self.assertIsNone(self.delivered)
            embed = kwargs['embed']
            self.assertLessEqual(len(embed.description), 4096)
            self.assertLessEqual(len(embed.title), 256)
            self.assertLessEqual(
                len(embed.description) + len(embed.title) + len(embed.footer)
                + sum(len(field['name']) + len(field['value']) for field in embed.fields),
                6000,
            )

        self.channel.send.side_effect = send
        await self.manager.query_database()
        self.assertEqual(self.channel.send.await_count, len(expected))
        complete = ''.join(embed.description for embed in expected)
        self.assertIn(self.manager._escape_discord_markdown(details), complete)
        self.assertEqual(self.delivered, '2026-11-02')

    async def test_partial_delivery_failure_does_not_mark_date(self):
        self.stream_manager.catalogue['offline']['details'] = 'x' * 8000
        self.channel.send.side_effect = [None, RuntimeError('second page failed')]
        await self.manager.query_database()
        self.assertIsNone(self.delivered)
        self.stream_manager.mark_daily_announced.assert_not_called()
        self.channel.send.side_effect = None
        await self.manager.query_database()
        self.assertEqual(self.delivered, '2026-11-02')

    async def test_concurrent_queries_do_not_send_concurrently(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def send(**kwargs):
            entered.set()
            await release.wait()

        self.channel.send.side_effect = send
        first = asyncio.create_task(self.manager.query_database())
        try:
            await entered.wait()
            await self.manager.query_database()
            self.assertEqual(self.channel.send.await_count, 1)
        finally:
            release.set()
            await first
        self.stream_manager.mark_daily_announced.assert_called_once_with('2026-11-02')

    async def test_state_storage_failure_is_not_reported_as_success(self):
        self.stream_manager.mark_daily_announced.side_effect = OSError('disk unavailable')
        await self.manager.query_database()
        self.assertIsNone(self.delivered)
        self.manager.logger.exception.assert_called_once()
        self.stream_manager.mark_daily_announced.side_effect = self.mark_delivered
        await self.manager.query_database()
        self.assertEqual(self.delivered, '2026-11-02')

    async def test_reconnect_reuses_scheduler_and_requests_immediate_query(self):
        events = {}
        self.manager.bot.event = lambda func: events.setdefault(func.__name__, func)
        self.manager._startup_greeting_sent = True
        job = Mock()
        self.manager.scheduler = Mock(running=False)
        self.manager.scheduler.get_job.side_effect = [None, job]
        self.manager.scheduler.add_job.return_value = job
        self.manager._setup_bot_events()
        await events['on_ready']()
        self.manager.scheduler.running = True
        await events['on_ready']()
        self.manager.scheduler.start.assert_called_once()
        self.manager.scheduler.add_job.assert_called_once()
        self.assertEqual(job.modify.call_count, 2)


if __name__ == '__main__':
    unittest.main()
