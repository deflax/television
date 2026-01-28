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

        # Track bot messages per channel (keep last N message IDs)
        self.max_channel_messages = 5
        self._channel_messages = {}  # channel_id -> deque of discord.Message

        # Setup bot commands and events
        self._setup_bot_events()
        self._setup_bot_commands()

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

        @self.bot.command(name='hello', help='Say hello to the bot')
        @has_role(self.worshipper_role_name)
        async def hello(ctx):
            author_name = ctx.author.name
            await ctx.channel.send(f'Hi, {author_name} :blush:')

        @hello.error
        async def hello_error(ctx, error):
            if isinstance(error, CheckFailure):
                await ctx.channel.send('Do I know you?')

        @self.bot.command(name='time', help='Show current time in UTC')
        async def time(ctx):
            await ctx.channel.send(f'The Coordinated Universal Time is `{datetime.now(timezone.utc)}`')

        @self.bot.command(name='epg', help='Lists scheduled streams')
        async def epg(ctx):
            if self.database != {}:
                scheduled_list = ""
                live_list = ""
                for key, value in self.database.items():
                    item_name = value['name']
                    item_start = value['start_at']
                    if item_start != 'now' and item_start != 'never':
                        scheduled_list += f'- {item_name} starts at {item_start}:00 UTC\n'
                    else:
                        live_list += f'- {item_name} is LIVE\n'
                await ctx.channel.send(f'```{scheduled_list}```')
                if live_list != "":
                    await ctx.channel.send(f'```{live_list}```')
            else:
                await ctx.channel.send('```Empty.```')

        @self.bot.command(name='now', help='Displays whats playing right now')
        async def now(ctx):
            playhead = await self.query_playhead()
            stream_name = playhead['name']
            await ctx.channel.send(f'Now playing {stream_name}')

        @self.bot.command(name='rec', help='Start the recorder')
        @has_role(self.boss_role_name)
        async def rec(ctx):
            # Check if the recorder job already exists
            if self.recorder:
                await ctx.channel.send(f'Recorder is busy!')
            else:
                playhead = await self.query_playhead()
                stream_name = playhead['name']
                self.recorder = True
                await ctx.channel.send(f'Recording from {stream_name}...')
                await self.exec_recorder(playhead)

        @rec.error
        async def rec_error(ctx, error):
            if isinstance(error, CheckFailure):
                await ctx.channel.send('Access denied!')

        @self.bot.command(name='stop', help='Stop the recorder')
        @has_role(self.boss_role_name)
        async def stop(ctx):
            # Check if the recorder job already exists
            if self.recorder:
                await ctx.channel.send(f'Shutting down recorder...')
                # TODO: kill any process currently running
                self.recorder = False
            else:
                await ctx.channel.send(f'Recorder is already stopped.')

        @stop.error
        async def stop_error(ctx, error):
            if isinstance(error, CheckFailure):
                await ctx.channel.send('Access denied!')

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
            await self._send_and_prune(live_channel, content=f'{stream_name} is live! :satellite_orbital: {stream_details}')
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

            # message = (
            #     f"🔐 **Access Request**\n"
            #     f"**Hostname**: `{obfuscated_hostname}`\n"
            #     f"**Timecode**: `{timecode}`"
            # )
            message = f"🔐 `{obfuscated_hostname}` `timecode: {timecode}`"
            await self._send_and_prune(channel, content=message)
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
