import os
import asyncio
import logging
import json
from collections import deque
from datetime import datetime, timezone
from typing import Optional
import discord
from discord.ext.commands import Bot, has_permissions, CheckFailure, has_role, MissingRole
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from ffmpeg import FFmpeg, Progress
from obfuscation import obfuscate_hostname


class DiscordBotManager:
    """Manages Discord bot functionality integrated with the Flask API."""

    def __init__(self, config, logger: logging.Logger, stream_manager):
        self.config = config
        self.logger = logger
        self.stream_manager = stream_manager

        # Read Discord-specific env variables
        self.bot_token = os.environ.get('DISCORDBOT_TOKEN', 'token')
        self.live_channel_id = os.environ.get('DISCORDBOT_LIVE_CHANNEL_ID', 0)
        self.timecode_channel_id = os.environ.get('DISCORDBOT_TIMECODE_CHANNEL_ID', 0)
        self.live_channel_update = os.environ.get('DISCORDBOT_LIVE_CHANNEL_UPDATE', 1440)
        self.scheduler_hostname = os.environ.get('SERVER_NAME', 'example.com')

        # Discord API Intents
        intents = discord.Intents.all()
        intents.members = True
        intents.guilds = True
        intents.messages = True
        intents.reactions = True
        intents.presences = True
        intents.message_content = True

        # Discord client
        self.bot = Bot(command_prefix=".", intents=intents)
        self.worshipper_role_name = "worshipper"
        self.boss_role_name = "bosmang"

        # Scheduler for Discord tasks
        self.scheduler = AsyncIOScheduler()

        # Internal state
        self.database = {}
        self.rec_path = "/recordings"
        self.recorder = False
        self.visitor_tracker = None

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

        @self.bot.command(name='start', help='Start a Restreamer process. Usage: .start <name or process_id>')
        @has_role(self.boss_role_name)
        async def start(ctx, *, identifier: str = None):
            if not identifier:
                embed = self._make_embed(
                    title='⚠️ Usage',
                    description='`.start <name or process_id>`\nUse `.streams` to list available streams.',
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
            result = self.stream_manager.process_command(process_id, 'start')
            if result['success']:
                embed = self._make_embed(
                    title='▶️ Stream Started',
                    description=f'**{display_name}**',
                    color=self.COLOR_SUCCESS,
                    footer=f'ID: {process_id}'
                )
            else:
                embed = self._make_embed(
                    title='❌ Start Failed',
                    description=f'**{display_name}**\n{result["message"]}',
                    color=self.COLOR_ERROR,
                    footer=f'ID: {process_id}'
                )
            await ctx.channel.send(embed=embed)

        @start.error
        async def start_error(ctx, error):
            if isinstance(error, CheckFailure):
                embed = self._make_embed(title='🚫 Access Denied', color=self.COLOR_ERROR)
                await ctx.channel.send(embed=embed)

        @self.bot.command(name='stop', help='Stop a Restreamer process. Usage: .stop <name or process_id>')
        @has_role(self.boss_role_name)
        async def stop(ctx, *, identifier: str = None):
            if not identifier:
                embed = self._make_embed(
                    title='⚠️ Usage',
                    description='`.stop <name or process_id>`\nUse `.streams` to list available streams.',
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
            result = self.stream_manager.process_command(process_id, 'stop')
            if result['success']:
                embed = self._make_embed(
                    title='⏹️ Stream Stopped',
                    description=f'**{display_name}**',
                    color=self.COLOR_NEUTRAL,
                    footer=f'ID: {process_id}'
                )
            else:
                embed = self._make_embed(
                    title='❌ Stop Failed',
                    description=f'**{display_name}**\n{result["message"]}',
                    color=self.COLOR_ERROR,
                    footer=f'ID: {process_id}'
                )
            await ctx.channel.send(embed=embed)

        @stop.error
        async def stop_error(ctx, error):
            if isinstance(error, CheckFailure):
                embed = self._make_embed(title='🚫 Access Denied', color=self.COLOR_ERROR)
                await ctx.channel.send(embed=embed)

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

            for key, value in self.database.items():
                item_name = value['name']
                item_start = value['start_at']
                if item_start == 'now':
                    live_streams.append(f'🔴 **{item_name}**')
                elif item_start != 'never':
                    item_str = str(item_start).strip()
                    if len(item_str) <= 2:
                        display_time = item_str.zfill(2) + ':00'
                    else:
                        display_time = item_str[:-2].zfill(2) + ':' + item_str[-2:]
                    scheduled_streams.append(f'⏰ {item_name} — `{display_time} UTC`')

            if live_streams:
                fields.append({'name': 'Live Now', 'value': '\n'.join(live_streams), 'inline': False})
            if scheduled_streams:
                fields.append({'name': 'Scheduled', 'value': '\n'.join(scheduled_streams), 'inline': False})

            embed = self._make_embed(
                title='📺 Schedule',
                color=self.COLOR_SUCCESS if live_streams else self.COLOR_INFO,
                fields=fields
            )
            await ctx.channel.send(embed=embed)

        @self.bot.command(name='watchers', help='Lists all current watcher hostnames')
        @has_role(self.boss_role_name)
        async def watchers(ctx):
            if self.visitor_tracker is None:
                embed = self._make_embed(
                    title='👁️ Watchers',
                    description='Visitor tracker not available.',
                    color=self.COLOR_WARNING
                )
                await ctx.channel.send(embed=embed)
                return

            current_visitors = self.visitor_tracker.visitors
            if not current_visitors:
                embed = self._make_embed(
                    title='👁️ Watchers',
                    description='No watchers connected.',
                    color=self.COLOR_NEUTRAL
                )
                await ctx.channel.send(embed=embed)
                return

            watcher_lines = []
            for ip in current_visitors:
                hostname = obfuscate_hostname(ip, ip)
                connections = current_visitors[ip]
                if connections > 1:
                    watcher_lines.append(f'`{hostname}` ×{connections}')
                else:
                    watcher_lines.append(f'`{hostname}`')

            total = len(current_visitors)
            embed = self._make_embed(
                title=f'👁️ Watchers ({total})',
                description='\n'.join(watcher_lines),
                color=self.COLOR_INFO
            )
            await ctx.channel.send(embed=embed)

        @watchers.error
        async def watchers_error(ctx, error):
            if isinstance(error, CheckFailure):
                embed = self._make_embed(title='🚫 Access Denied', color=self.COLOR_ERROR)
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

        @rnd.error
        async def rnd_error(ctx, error):
            if isinstance(error, CheckFailure):
                embed = self._make_embed(title='🚫 Access Denied', color=self.COLOR_ERROR)
                await ctx.channel.send(embed=embed)

        @self.bot.command(name='rec', help='Start the recorder')
        @has_role(self.boss_role_name)
        async def rec(ctx):
            if self.recorder:
                embed = self._make_embed(
                    title='⚠️ Recorder Busy',
                    description='A recording is already in progress.',
                    color=self.COLOR_WARNING
                )
                await ctx.channel.send(embed=embed)
            else:
                playhead = await self.query_playhead()
                stream_name = playhead.get('name', 'Unknown')
                self.recorder = True
                embed = self._make_embed(
                    title='⏺️ Recording Started',
                    description=f'Recording from **{stream_name}**...',
                    color=self.COLOR_SUCCESS
                )
                await ctx.channel.send(embed=embed)
                await self.exec_recorder(playhead)

        @rec.error
        async def rec_error(ctx, error):
            if isinstance(error, CheckFailure):
                embed = self._make_embed(title='🚫 Access Denied', color=self.COLOR_ERROR)
                await ctx.channel.send(embed=embed)

        @self.bot.command(name='recstop', help='Stop the recorder')
        @has_role(self.boss_role_name)
        async def recstop(ctx):
            if self.recorder:
                # TODO: kill any process currently running
                self.recorder = False
                embed = self._make_embed(
                    title='⏹️ Recording Stopped',
                    description='The recorder has been stopped.',
                    color=self.COLOR_NEUTRAL
                )
            else:
                embed = self._make_embed(
                    title='ℹ️ Recorder Idle',
                    description='The recorder is not running.',
                    color=self.COLOR_NEUTRAL
                )
            await ctx.channel.send(embed=embed)

        @recstop.error
        async def recstop_error(ctx, error):
            if isinstance(error, CheckFailure):
                embed = self._make_embed(title='🚫 Access Denied', color=self.COLOR_ERROR)
                await ctx.channel.send(embed=embed)

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
                        minutes=int(self.live_channel_update),
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
            live_channel = self.bot.get_channel(int(self.live_channel_id))
            await live_channel.send(f'{stream_name} is live! :satellite_orbital: {stream_details}')
        self.logger.info(f'{stream_name} is live! {stream_details}')

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
            channel = self.bot.get_channel(int(self.timecode_channel_id))
            if channel is None:
                self.logger.error(f'Could not find Discord channel with ID {self.timecode_channel_id}')
                return

            message = f"🔐 `{obfuscated_hostname}` `timecode: {timecode}`"
            await channel.send(message)
            self.logger.info(f'Sent timecode to Discord for {obfuscated_hostname}')
        except Exception as e:
            self.logger.error(f'Failed to send timecode message to Discord: {e}')

    def log_visitor_connect(self, ip: str, visitor_count: int) -> bool:
        """Log visitor connection to Discord channel (thread-safe, sync method).

        Args:
            ip: The visitor's IP address (will be obfuscated)
            visitor_count: Current total visitor count

        Returns:
            True if message was scheduled, False otherwise.
        """
        if self.live_channel_id == 0:
            return False

        if not self.bot.is_ready():
            self.logger.warning('Discord bot is not ready yet')
            return False

        obfuscated_ip = obfuscate_hostname(ip, ip)
        try:
            asyncio.run_coroutine_threadsafe(
                self._log_visitor_async(obfuscated_ip, visitor_count, connected=True),
                self.bot.loop
            )
            return True
        except Exception as e:
            self.logger.error(f'Failed to schedule visitor connect message: {e}')
            return False

    def log_visitor_disconnect(self, ip: str, visitor_count: int) -> bool:
        """Log visitor disconnection to Discord channel (thread-safe, sync method).

        Args:
            ip: The visitor's IP address (will be obfuscated)
            visitor_count: Current total visitor count

        Returns:
            True if message was scheduled, False otherwise.
        """
        if self.live_channel_id == 0:
            return False

        if not self.bot.is_ready():
            self.logger.warning('Discord bot is not ready yet')
            return False

        obfuscated_ip = obfuscate_hostname(ip, ip)
        try:
            asyncio.run_coroutine_threadsafe(
                self._log_visitor_async(obfuscated_ip, visitor_count, connected=False),
                self.bot.loop
            )
            return True
        except Exception as e:
            self.logger.error(f'Failed to schedule visitor disconnect message: {e}')
            return False

    async def _log_visitor_async(self, obfuscated_ip: str, visitor_count: int, connected: bool):
        """Internal async method to log visitor event to Discord."""
        try:
            channel = self.bot.get_channel(int(self.live_channel_id))
            if channel is None:
                self.logger.error(f'Could not find Discord channel with ID {self.live_channel_id}')
                return

            if connected:
                message = f"📥 `{obfuscated_ip}` 👽 `{visitor_count}`"
            else:
                message = f"📤 `{obfuscated_ip}` 👽 `{visitor_count}`"

            await self._send_and_prune(channel, content=message)
        except Exception as e:
            self.logger.error(f'Failed to send visitor log to Discord: {e}')

    async def exec_recorder(self, playhead):
        """Execute the recorder to capture a stream."""
        stream_id = playhead['id']
        stream_name = playhead['name']
        stream_hls_url = playhead['head']
        current_datetime = datetime.now().strftime("%Y%m%d_%H%M%S")
        video_file = current_datetime + ".mp4"
        thumb_file = current_datetime + ".png"
        video_output = f'{self.rec_path}/live/{video_file}'
        thumb_output = f'{self.rec_path}/live/{thumb_file}'

        try:
            self.logger.info(f'Recording video {video_file}')
            # Record a mp4 file
            ffmpeg = (
                FFmpeg()
                .option("y")
                .input(stream_hls_url)
                .output(video_output,
                        {"codec:v": "copy", "codec:a": "copy", "bsf:a": "aac_adtstoasc"},
                ))

            @ffmpeg.on("progress")
            def on_progress(progress: Progress):
                self.logger.info(progress)

            ffmpeg.execute()
            self.logger.info(f'Recording of {video_file} finished.')

        except Exception as joberror:
            self.logger.error(f'Recording of {video_file} failed!')
            self.logger.error(joberror)

        else:
            # Show Metadata
            ffmpeg_metadata = (
                FFmpeg(executable="ffprobe")
                .input(video_output,
                       print_format="json",
                       show_streams=None,)
            )
            media = json.loads(ffmpeg_metadata.execute())
            self.logger.info(f"# Video")
            self.logger.info(f"- Codec: {media['streams'][0]['codec_name']}")
            self.logger.info(f"- Resolution: {media['streams'][0]['width']} X {media['streams'][0]['height']}")
            self.logger.info(f"- Duration: {media['streams'][0]['duration']}")
            self.logger.info(f"# Audio")
            self.logger.info(f"- Codec: {media['streams'][1]['codec_name']}")
            self.logger.info(f"- Sample Rate: {media['streams'][1]['sample_rate']}")
            self.logger.info(f"- Duration: {media['streams'][1]['duration']}")

            thumb_skip_time = float(media['streams'][0]['duration']) // 2
            thumb_width = media['streams'][0]['width']

        try:
            self.logger.info(f'Generating thumb {thumb_file}')
            # Generate thumbnail image from the recorded mp4 file
            ffmpeg_thumb = (
                FFmpeg()
                .input(video_output, ss=thumb_skip_time)
                .output(thumb_output, vf='scale={}:{}'.format(thumb_width, -1), vframes=1)
            )
            ffmpeg_thumb.execute()
            self.logger.info(f'Thumbnail {thumb_file} created.')

        except Exception as joberror:
            self.logger.error(f'Generating thumb {thumb_file} failed!')
            self.logger.error(joberror)

        # When ready, move the recorded from the live dir to the archives and reset the rec head
        os.rename(f'{video_output}', f'{self.rec_path}/vod/{video_file}')
        os.rename(f'{thumb_output}', f'{self.rec_path}/thumb/{thumb_file}')
        await self.create_embed(stream_name, video_file, thumb_file)
        self.logger.info('Recording job done')
        self.recorder = False

    async def create_embed(self, stream_name, video_filename, thumb_filename):
        """Create a Discord embed for a recorded video."""
        img_url = f'https://{self.scheduler_hostname}/static/images'
        thumb_url = f'https://{self.scheduler_hostname}/thumb/{thumb_filename}'
        video_download_url = f'https://{self.scheduler_hostname}/video/download/{video_filename}'
        video_filename_no_extension = video_filename.split('.')[0]
        video_watch_url = f'https://{self.scheduler_hostname}/video/watch/{video_filename_no_extension}'

        embed = discord.Embed(
            title=f'VOD: {video_filename_no_extension}',
            url=f'{video_watch_url}',
            description=f'{stream_name}',
            colour=0x00b0f4,
            timestamp=datetime.now()
        )
        embed.add_field(
            name="Download",
            value=f'[mp4 file]({video_download_url})',
            inline=True
        )
        embed.add_field(
            name="Watch",
            value=f'[plyr.js player]({video_watch_url}) :]',
            inline=True
        )
        embed.set_image(url=thumb_url)
        embed.set_footer(
            text="DeflaxTV",
            icon_url=f'{img_url}/logo-96.png'
        )

        if self.live_channel_id != 0:
            live_channel = self.bot.get_channel(int(self.live_channel_id))
            await live_channel.send(embed=embed)

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
