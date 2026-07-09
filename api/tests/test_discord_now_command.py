import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path


api_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(api_dir))


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
    _ = sys.modules.setdefault('discord.ext', ext_module)
    _ = sys.modules.setdefault('discord.ext.commands', commands_module)

    scheduler_module = types.ModuleType('apscheduler.schedulers.asyncio')

    class AsyncIOScheduler:
        pass

    setattr(scheduler_module, 'AsyncIOScheduler', AsyncIOScheduler)
    sys.modules.setdefault('apscheduler', types.ModuleType('apscheduler'))
    sys.modules.setdefault('apscheduler.schedulers', types.ModuleType('apscheduler.schedulers'))
    sys.modules.setdefault('apscheduler.schedulers.asyncio', scheduler_module)

    spec = importlib.util.spec_from_file_location(
        'discord_bot_manager_under_test',
        integrations_dir / 'discord_bot_manager.py',
    )
    if spec is None or spec.loader is None:
        raise RuntimeError('could not load discord_bot_manager.py')

    module = importlib.util.module_from_spec(spec)
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


if __name__ == '__main__':
    unittest.main()
