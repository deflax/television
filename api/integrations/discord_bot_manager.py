import os
import asyncio
import logging
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Optional
import discord
from discord.ext.commands import Bot, CheckFailure, has_role
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from utils.obfuscation import obfuscate_hostname


class DiscordBotManager:
    """Manages Discord bot functionality integrated with the Flask API."""

    @staticmethod
    def _read_int_env(name: str, default: int, logger: logging.Logger) -> int:
        """Read an integer environment variable with fallback."""
        raw_value = os.environ.get(name)
        if raw_value in (None, ''):
            return default

        try:
            return int(raw_value)
        except ValueError:
            logger.warning(f'Invalid integer for {name}: {raw_value!r}. Using default {default}.')
            return default

    def __init__(self, config, logger: logging.Logger, stream_manager):
        self.config = config
        self.logger = logger
        self.stream_manager = stream_manager

        # Read Discord-specific env variables
        self.bot_token = os.environ.get('DISCORDBOT_TOKEN', 'token')
        self.live_channel_id = self._read_int_env('DISCORDBOT_LIVE_CHANNEL_ID', 0, self.logger)
        self.timecode_channel_id = self._read_int_env('DISCORDBOT_TIMECODE_CHANNEL_ID', 0, self.logger)
        self.live_channel_update = self._read_int_env('DISCORDBOT_LIVE_CHANNEL_UPDATE', 1440, self.logger)
        self.scheduler_hostname = os.environ.get('SERVER_NAME', 'example.com')

        # Discord client
        self.bot = Bot(command_prefix=".", intents=discord.Intents.all())
        self.worshipper_role_name = "worshipper"
        self.boss_role_name = "bosmang"

        # Scheduler for Discord tasks
        self.scheduler = AsyncIOScheduler()

        # Internal state
        self.database = {}
        self.visitor_tracker = None
        self.hls_viewer_ips: set = set()  # IPs of HLS-only viewers (not connected via SSE)

        # Clear log deletion pacing to avoid Discord API spam
        self.clearlog_bulk_size = 100
        self.clearlog_bulk_pause_seconds = 1.0
        self.clearlog_single_pause_seconds = 0.4

        # Track bot messages per channel (keep last N message IDs)
        self.max_channel_messages = 1
        self._channel_messages = {}  # channel_id -> deque of discord.Message

        # Setup bot commands and events
        self._setup_bot_events()
        self._setup_bot_commands()

    # Embed color constants
    COLOR_SUCCESS = 0x2ecc71  # Green
    COLOR_ERROR = 0xe74c3c    # Red
    COLOR_WARNING = 0xf39c12  # Orange
    COLOR_INFO = 0x3498db     # Blue
    COLOR_NEUTRAL = 0x95a5a6  # Gray

    def _make_embed(self, title: str, description: str = None, color: int = None, 
                    fields: list = None, footer: str = None) -> discord.Embed:
        """Create a standardized embed."""
        embed = discord.Embed(
            title=title,
            description=description,
            colour=color or self.COLOR_INFO,
            timestamp=datetime.now(timezone.utc)
        )
        if fields:
            for field in fields:
                embed.add_field(
                    name=field.get('name', ''),
                    value=field.get('value', ''),
                    inline=field.get('inline', False)
                )
        if footer:
            embed.set_footer(text=footer)
        return embed

    @staticmethod
    def _format_schedule_time(start_at: str) -> str:
        """Format a military time or keyword into a display string."""
        if start_at == 'now':
            return 'Live now'
        if start_at == 'never':
            return 'Unscheduled'
        time_str = str(start_at).strip()
        if len(time_str) <= 2:
            return f'{time_str.zfill(2)}:00 UTC'
        return f'{time_str[:-2].zfill(2)}:{time_str[-2:]} UTC'

    @staticmethod
    def _schedule_sort_minutes(start_at: str) -> int:
        """Convert schedule time to minutes from midnight for sorting."""
        try:
            time_str = str(start_at).strip()
            if len(time_str) <= 2:
                hour, minute = int(time_str), 0
            else:
                hour, minute = int(time_str[:-2]), int(time_str[-2:])

            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError('Invalid time range')

            return (hour * 60) + minute
        except (TypeError, ValueError):
            # Keep malformed schedule values at the end of the list.
            return (24 * 60) + 1

    def _schedule_async(self, coro, error_msg: str) -> bool:
        """Schedule a coroutine on the bot event loop (thread-safe).

        Common guard: returns False if live channel is unconfigured or bot is not ready.
        """
        if self.live_channel_id == 0:
            return False
        if not self.bot.is_ready():
            self.logger.warning('Discord bot is not ready yet')
            return False
        try:
            asyncio.run_coroutine_threadsafe(coro, self.bot.loop)
            return True
        except Exception as e:
            self.logger.error(f'{error_msg}: {e}')
            return False

    def _resolve_process(self, identifier: str) -> Optional[dict]:
        """Resolve a stream name or process ID to a process dict.

        Tries an exact process ID match first, then a case-insensitive
        name match against the live process list from Restreamer.
        """
        process_list = self.stream_manager.get_core_process_list()
        # Exact ID match
        for p in process_list:
            if p['id'] == identifier:
                return p
        # Case-insensitive name match
        identifier_lower = identifier.lower()
        for p in process_list:
            if p['name'].lower() == identifier_lower:
                return p
        return None

    def _setup_bot_events(self):
        """Setup Discord bot events."""

        @self.bot.event
        async def on_ready():
            self.scheduler.start()

            # Schedule a database update
            self.scheduler.add_job(
                func=self.query_database,
                trigger='interval',
                seconds=15,
                id='query_database'
            )
            self.scheduler.get_job('query_database').modify(next_run_time=datetime.now())
            self.logger.info(f'Discord bot logged in as {self.bot.user}')

    def _setup_bot_commands(self):
        """Setup Discord bot commands."""

        @self.bot.command(name='streams', help='List all Restreamer processes and their states')
        @has_role(self.boss_role_name)
        async def streams(ctx):
            process_list = self.stream_manager.get_core_process_list()
            if not process_list:
                await ctx.channel.send('```No processes found.```')
                return

            # Separate running and stopped streams
            running = [p for p in process_list if p['state'] == 'running']
            stopped = [p for p in process_list if p['state'] != 'running']

            # Build embed
            embed = discord.Embed(
                title='Streams',
                colour=0x00b0f4 if running else 0x95a5a6,
                timestamp=datetime.now(timezone.utc)
            )

            # Running streams
            if running:
                running_lines = '\n'.join(f"▶ **{p['name']}**\n`{p['id']}`" for p in running)
                embed.add_field(name=f'🟢 Running ({len(running)})', value=running_lines, inline=False)

            # Stopped streams
            if stopped:
                stopped_lines = '\n'.join(f"⏹ {p['name']}\n`{p['id']}`" for p in stopped)
                embed.add_field(name=f'🔴 Stopped ({len(stopped)})', value=stopped_lines, inline=False)

            embed.set_footer(text=f'Total: {len(process_list)} streams')
            await ctx.channel.send(embed=embed)

        @streams.error
        async def streams_error(ctx, error):
            if isinstance(error, CheckFailure):
                await ctx.channel.send('Access denied!')

        # Shared error handler for role-gated commands
        async def _access_denied(ctx, error):
            if isinstance(error, CheckFailure):
                embed = self._make_embed(title='🚫 Access Denied', color=self.COLOR_ERROR)
                await ctx.channel.send(embed=embed)

        async def _process_command(ctx, identifier, command):
            """Shared handler for .start and .stop commands."""
            if not identifier:
                embed = self._make_embed(
                    title='⚠️ Usage',
                    description=f'`.{command} <name or process_id>`\nUse `.streams` to list available streams.',
                    color=self.COLOR_WARNING
                )
                await ctx.channel.send(embed=embed)
                return
            process = self._resolve_process(identifier)
            if not process:
                embed = self._make_embed(
                    title='⚠️ Not Found',
                    description=f'No stream found matching `{identifier}`\nUse `.streams` to list available streams.',
                    color=self.COLOR_WARNING
                )
                await ctx.channel.send(embed=embed)
                return
            process_id = process['id']
            display_name = process['name']
            result = self.stream_manager.process_command(process_id, command)
            if not result['success']:
                embed = self._make_embed(
                    title=f'❌ {command.capitalize()} Failed',
                    description=f'**{display_name}**\n{result["message"]}',
                    color=self.COLOR_ERROR, footer=f'ID: {process_id}'
                )
                await ctx.channel.send(embed=embed)

        @self.bot.command(name='start', help='Start a Restreamer process. Usage: .start <name or process_id>')
        @has_role(self.boss_role_name)
        async def start(ctx, *, identifier: str = None):
            await _process_command(ctx, identifier, 'start')

        start.error(_access_denied)

        @self.bot.command(name='stop', help='Stop a Restreamer process. Usage: .stop <name or process_id>')
        @has_role(self.boss_role_name)
        async def stop(ctx, *, identifier: str = None):
            await _process_command(ctx, identifier, 'stop')

        stop.error(_access_denied)

        @self.bot.command(name='hello', help='Say hello to the bot')
        @has_role(self.worshipper_role_name)
        async def hello(ctx):
            author_name = ctx.author.name
            embed = self._make_embed(
                title=f'👋 Hello, {author_name}!',
                color=self.COLOR_INFO
            )
            await ctx.channel.send(embed=embed)

        @hello.error
        async def hello_error(ctx, error):
            if isinstance(error, CheckFailure):
                embed = self._make_embed(
                    title='🤔 Do I know you?',
                    color=self.COLOR_NEUTRAL
                )
                await ctx.channel.send(embed=embed)

        @self.bot.command(name='time', help='Show current time in UTC')
        async def time(ctx):
            now = datetime.now(timezone.utc)
            embed = self._make_embed(
                title='🕐 Server Time',
                description=f'`{now.strftime("%Y-%m-%d %H:%M:%S")} UTC`',
                color=self.COLOR_INFO
            )
            await ctx.channel.send(embed=embed)

        @self.bot.command(name='epg', help='Lists scheduled streams')
        async def epg(ctx):
            if not self.database:
                embed = self._make_embed(
                    title='📺 Schedule',
                    description='No streams scheduled.',
                    color=self.COLOR_NEUTRAL
                )
                await ctx.channel.send(embed=embed)
                return

            fields = []
            live_streams = []
            scheduled_streams = []

            for value in self.database.values():
                item_name = value['name']
                item_start = value['start_at']
                if item_start == 'now':
                    live_streams.append(f'🔴 **{item_name}**')
                elif item_start != 'never':
                    scheduled_streams.append({
                        'name': item_name,
                        'start_at': item_start,
                    })

            scheduled_streams.sort(
                key=lambda item: (
                    self._schedule_sort_minutes(item['start_at']),
                    item['name'].lower(),
                )
            )
            scheduled_lines = [
                f"⏰ `{self._format_schedule_time(item['start_at'])}` — {item['name']}"
                for item in scheduled_streams
            ]

            if live_streams:
                fields.append({'name': 'Live Now', 'value': '\n'.join(live_streams), 'inline': False})
            if scheduled_lines:
                fields.append({'name': 'Scheduled', 'value': '\n'.join(scheduled_lines), 'inline': False})

            embed = self._make_embed(
                title='📺 Schedule',
                color=self.COLOR_SUCCESS if live_streams else self.COLOR_INFO,
                fields=fields
            )
            await ctx.channel.send(embed=embed)

        @self.bot.command(name='now', help='Displays whats playing right now')
        async def now(ctx):
            playhead = await self.query_playhead()
            stream_name = playhead.get('name', 'Unknown')
            embed = self._make_embed(
                title='▶️ Now Playing',
                description=f'**{stream_name}**',
                color=self.COLOR_SUCCESS
            )
            await ctx.channel.send(embed=embed)

        @self.bot.command(name='rnd', help='Switch to a random stream from the database')
        @has_role(self.boss_role_name)
        async def rnd(ctx):
            # Check if there's only one stream (or none) in the database
            if len(self.stream_manager.database) <= 1:
                embed = self._make_embed(
                    title='⚠️ No Streams Available',
                    description='There are no other streams in the database to switch to.',
                    color=self.COLOR_WARNING
                )
                await ctx.channel.send(embed=embed)
                return

            next_stream = self.stream_manager.get_next_stream()
            if not next_stream:
                embed = self._make_embed(
                    title='⚠️ No Streams Available',
                    description='There are no other streams in the database to switch to.',
                    color=self.COLOR_WARNING
                )
                await ctx.channel.send(embed=embed)
                return
            
            # Execute the stream switch
            self.stream_manager.exec_stream(
                next_stream['stream_id'],
                next_stream['stream_name'],
                next_stream['stream_prio'],
                next_stream['stream_hls_url']
            )
            
            embed = self._make_embed(
                title='🎲 Random Stream',
                description=f'Now playing: **{next_stream["stream_name"]}**',
                color=self.COLOR_SUCCESS,
                footer=f'ID: {next_stream["stream_id"]}'
            )
            await ctx.channel.send(embed=embed)

        rnd.error(_access_denied)

        @self.bot.command(name='clearlog', help='Delete previous bot messages in this channel')
        @has_role(self.boss_role_name)
        async def clearlog(ctx):
            try:
                await ctx.message.add_reaction('🧹')
            except (discord.Forbidden, discord.HTTPException):
                pass

            deleted_count, failed_count = await self._clear_bot_messages(ctx.channel)

            status_emoji = '✅' if failed_count == 0 else '⚠️'
            try:
                await ctx.message.add_reaction(status_emoji)
            except (discord.Forbidden, discord.HTTPException):
                pass

            color = self.COLOR_SUCCESS if failed_count == 0 else self.COLOR_WARNING
            description = f'Deleted `{deleted_count}` bot messages.'
            if failed_count > 0:
                description += f'\nSkipped `{failed_count}` messages due to permissions or API errors.'

            embed = self._make_embed(
                title='🧹 Clear Log Complete',
                description=description,
                color=color
            )
            await ctx.channel.send(embed=embed, delete_after=10)

        clearlog.error(_access_denied)

    async def _send_and_prune(self, channel, content=None, embed=None):
        """Send a message to a channel and delete oldest messages beyond the limit.

        Keeps only the last `self.max_channel_messages` bot messages per channel.
        Returns the sent message.
        """
        channel_id = channel.id
        if channel_id not in self._channel_messages:
            self._channel_messages[channel_id] = deque()

        # Send the new message
        msg = await channel.send(content=content, embed=embed)
        self._channel_messages[channel_id].append(msg)

        # Delete old messages beyond the limit
        while len(self._channel_messages[channel_id]) > self.max_channel_messages:
            old_msg = self._channel_messages[channel_id].popleft()
            try:
                await old_msg.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
                self.logger.warning(f'Failed to delete old message {old_msg.id}: {e}')

        return msg

    async def _prune_all(self, channel):
        """Delete all tracked bot messages for a channel without sending a new one."""
        channel_id = channel.id
        if channel_id not in self._channel_messages:
            return
        while self._channel_messages[channel_id]:
            old_msg = self._channel_messages[channel_id].popleft()
            try:
                await old_msg.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
                self.logger.warning(f'Failed to delete old message {old_msg.id}: {e}')

    async def _delete_single_message(self, message) -> bool:
        """Delete one message with error handling."""
        try:
            await message.delete()
            return True
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
            self.logger.warning(f'Failed to delete message {message.id}: {e}')
            return False

    async def _delete_message_batch(self, channel, messages: list) -> tuple[int, int]:
        """Delete up to 100 recent messages in one API call, with fallback."""
        if not messages:
            return 0, 0

        try:
            await channel.delete_messages(messages)
            return len(messages), 0
        except (discord.Forbidden, discord.HTTPException) as e:
            self.logger.warning(f'Bulk delete failed ({len(messages)} messages): {e}')

        deleted_count = 0
        failed_count = 0
        for message in messages:
            deleted = await self._delete_single_message(message)
            if deleted:
                deleted_count += 1
            else:
                failed_count += 1
            await asyncio.sleep(self.clearlog_single_pause_seconds)

        return deleted_count, failed_count

    async def _clear_bot_messages(self, channel) -> tuple[int, int]:
        """Remove all messages previously sent by this bot in a channel.

        Uses bulk deletion for messages newer than 14 days and throttled single
        deletes for older messages to reduce API pressure.
        """
        if self.bot.user is None:
            return 0, 0

        bulk_cutoff = datetime.now(timezone.utc) - timedelta(days=14)
        bulk_batch = []
        deleted_count = 0
        failed_count = 0

        async for message in channel.history(limit=None, oldest_first=False):
            if message.author.id != self.bot.user.id:
                continue

            if message.created_at > bulk_cutoff:
                bulk_batch.append(message)
                if len(bulk_batch) >= self.clearlog_bulk_size:
                    deleted, failed = await self._delete_message_batch(channel, bulk_batch)
                    deleted_count += deleted
                    failed_count += failed
                    bulk_batch.clear()
                    await asyncio.sleep(self.clearlog_bulk_pause_seconds)
                continue

            if bulk_batch:
                deleted, failed = await self._delete_message_batch(channel, bulk_batch)
                deleted_count += deleted
                failed_count += failed
                bulk_batch.clear()
                await asyncio.sleep(self.clearlog_bulk_pause_seconds)

            deleted = await self._delete_single_message(message)
            if deleted:
                deleted_count += 1
            else:
                failed_count += 1
            await asyncio.sleep(self.clearlog_single_pause_seconds)

        if bulk_batch:
            deleted, failed = await self._delete_message_batch(channel, bulk_batch)
            deleted_count += deleted
            failed_count += failed

        self._channel_messages[channel.id] = deque()
        return deleted_count, failed_count

    async def query_playhead(self):
        """Query the playhead from stream_manager."""
        # Get playhead directly from stream_manager
        playhead = self.stream_manager.playhead.copy()
        if playhead:
            head = playhead.get('head', 'unknown')
            self.logger.info(f'Playhead is at {head}')
        else:
            self.logger.warning('Playhead is empty')
        return playhead

    async def query_database(self):
        """Query the database from stream_manager and announce live streams."""
        # Get database directly from stream_manager
        self.database = self.stream_manager.database.copy()

        if self.database == {}:
            self.logger.error('Database is empty!')
            return

        # Search for streams and announce them
        for key, value in self.database.items():
            stream_name = value['name']
            stream_start_at = value['start_at']
            stream_details = value['details']
            if stream_start_at == 'now':
                # Announce live streams
                # Check if the announcement job already exists
                if self.scheduler.get_job('announce_live_channel') is None:
                    # Job doesn't exist, so add it
                    self.logger.info(f'{stream_name} live stream detected!')
                    self.scheduler.add_job(
                        func=self.announce_live_channel,
                        trigger='interval',
                        minutes=self.live_channel_update,
                        id='announce_live_channel',
                        args=(stream_name, stream_details)
                    )
                    self.scheduler.get_job('announce_live_channel').modify(next_run_time=datetime.now())
                    return
                else:
                    # Exit since we already have a live announcement job
                    return

        # Cleanup the announce job
        if self.scheduler.get_job('announce_live_channel') is not None:
            self.scheduler.remove_job('announce_live_channel')

    async def announce_live_channel(self, stream_name, stream_details):
        """Announce live stream to Discord channel."""
        if self.live_channel_id != 0:
            live_channel = self.bot.get_channel(self.live_channel_id)
            await live_channel.send(f'{stream_name} is live! :satellite_orbital: {stream_details}')
        self.logger.info(f'{stream_name} is live! {stream_details}')

    def announce_channel_added(self, stream_name: str, start_at: str, prio: int) -> bool:
        """Announce a channel addition to Discord (thread-safe)."""
        return self._schedule_async(
            self._announce_channel_change_async(stream_name, start_at, prio, added=True),
            'Failed to schedule channel added announcement'
        )

    def announce_channel_removed(self, stream_name: str) -> bool:
        """Announce a channel removal to Discord (thread-safe)."""
        return self._schedule_async(
            self._announce_channel_change_async(stream_name, added=False),
            'Failed to schedule channel removed announcement'
        )

    async def _announce_channel_change_async(self, stream_name: str, start_at: str = None,
                                              prio: int = None, added: bool = True):
        """Internal async method to announce channel addition/removal to Discord."""
        try:
            channel = self.bot.get_channel(self.live_channel_id)
            if channel is None:
                self.logger.error(f'Could not find Discord channel with ID {self.live_channel_id}')
                return

            if added:
                embed = self._make_embed(
                    title=':satellite_orbital: Channel Added',
                    description=f'**{stream_name}**',
                    color=self.COLOR_SUCCESS,
                    fields=[
                        {'name': 'Schedule', 'value': self._format_schedule_time(start_at), 'inline': True},
                        {'name': 'Priority', 'value': str(prio), 'inline': True},
                    ]
                )
            else:
                embed = self._make_embed(
                    title=':satellite_orbital: Channel Removed',
                    description=f'**{stream_name}**',
                    color=self.COLOR_NEUTRAL,
                )

            await channel.send(embed=embed)
        except Exception as e:
            self.logger.error(f'Failed to send channel change announcement to Discord: {e}')

    def send_timecode_message(self, obfuscated_hostname: str, timecode: str) -> bool:
        """Send timecode message to Discord channel (thread-safe, sync method).

        This method can be called from Flask routes (sync context) and will
        safely schedule the message to be sent by the Discord bot.

        Returns True if message was scheduled, False otherwise.
        """
        if self.timecode_channel_id == 0:
            self.logger.warning('Timecode channel ID not configured')
            return False

        if not self.bot.is_ready():
            self.logger.warning('Discord bot is not ready yet')
            return False

        # Get the bot's event loop and schedule the coroutine
        try:
            asyncio.run_coroutine_threadsafe(
                self._send_timecode_async(obfuscated_hostname, timecode),
                self.bot.loop
            )
            return True
        except Exception as e:
            self.logger.error(f'Failed to schedule timecode message: {e}')
            return False

    async def _send_timecode_async(self, obfuscated_hostname: str, timecode: str):
        """Internal async method to send timecode message to Discord."""
        try:
            channel = self.bot.get_channel(self.timecode_channel_id)
            if channel is None:
                self.logger.error(f'Could not find Discord channel with ID {self.timecode_channel_id}')
                return

            message = f"🔐 `{obfuscated_hostname}` `timecode: {timecode}`"
            await channel.send(message)
            self.logger.info(f'Sent timecode to Discord for {obfuscated_hostname}')
        except Exception as e:
            self.logger.error(f'Failed to send timecode message to Discord: {e}')

    def log_visitor_change(self) -> bool:
        """Send updated visitors embed on connect/disconnect (thread-safe)."""
        return self._schedule_async(
            self._send_visitors_embed(),
            'Failed to schedule visitor change message'
        )

    def _build_visitors_embed(self) -> discord.Embed:
        """Build the visitors embed from current tracker state.

        Shared by the .visitors command and automatic connect/disconnect announcements.
        """
        sse_visitors = self.visitor_tracker.visitors if self.visitor_tracker else {}
        hls_only_ips = self.hls_viewer_ips or set()

        if not sse_visitors and not hls_only_ips:
            return self._make_embed(
                title=':alien: Visitors',
                description='No visitors connected.',
                color=self.COLOR_NEUTRAL
            )

        visitor_lines = []

        for ip in sse_visitors:
            hostname = obfuscate_hostname(ip, ip)
            connections = sse_visitors[ip]
            if connections > 1:
                visitor_lines.append(f'🖥️ `{hostname}` ×{connections}')
            else:
                visitor_lines.append(f'🖥️ `{hostname}`')

        for ip in hls_only_ips:
            hostname = obfuscate_hostname(ip, ip)
            visitor_lines.append(f'📡 `{hostname}`')

        total = len(sse_visitors) + len(hls_only_ips)
        return self._make_embed(
            title=f':alien: Visitors ({total})',
            description='\n'.join(visitor_lines),
            footer='🖥️ Browser  📡 External player',
            color=self.COLOR_INFO
        )

    async def _send_visitors_embed(self):
        """Send the visitors embed to the live channel using send_and_prune.

        If no visitors are connected, deletes the previous message instead.
        """
        try:
            channel = self.bot.get_channel(self.live_channel_id)
            if channel is None:
                self.logger.error(f'Could not find Discord channel with ID {self.live_channel_id}')
                return

            sse_visitors = self.visitor_tracker.visitors if self.visitor_tracker else {}
            hls_only_ips = self.hls_viewer_ips or set()

            if not sse_visitors and not hls_only_ips:
                await self._prune_all(channel)
                return

            embed = self._build_visitors_embed()
            await self._send_and_prune(channel, embed=embed)
        except Exception as e:
            self.logger.error(f'Failed to send visitors embed to Discord: {e}')

    def update_hls_viewers(self, new_ips: set, total_count: int) -> None:
        """Update HLS viewer state and send visitors embed on changes."""
        old_ips = self.hls_viewer_ips
        self.hls_viewer_ips = new_ips

        if new_ips != old_ips:
            self._schedule_async(
                self._send_visitors_embed(),
                'Failed to schedule HLS visitors embed'
            )

    async def start(self):
        """Start the Discord bot."""
        try:
            await self.bot.start(self.bot_token)
        except Exception as e:
            self.logger.error(f'Discord bot failed to start: {e}')

    async def close(self):
        """Close the Discord bot."""
        await self.bot.close()
        self.scheduler.shutdown()
