from datetime import datetime, timezone
import os
import asyncio
import logging
import subprocess
import httpx
import discord
from discord.ext.commands import Bot, has_permissions, CheckFailure, has_role, MissingRole
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from ffmpeg import FFmpeg, Progress

# Read env variables
bot_token = os.environ.get('DISCORDBOT_TOKEN', 'token')
live_channel_id = os.environ.get('DISCORDBOT_LIVE_CHANNEL_ID', 0)
live_channel_update = os.environ.get('DISCORDBOT_LIVE_CHANNEL_UPDATE', 1440)
scheduler_hostname = os.environ.get('BASE_URL', 'example.com')

# Discord API Intents
intents = discord.Intents.all()
intents.members = True
intents.guilds = True
intents.messages = True
intents.reactions = True
intents.presences = True
intents.message_content = True

# Discord client
bot = Bot(command_prefix=".", intents=intents)
worshipper_role_name = "worshipper"
boss_role_name = "bosmang"

# Scheduler
scheduler = AsyncIOScheduler()

# Set up the logger
logger_discord = logging.getLogger('discord')
log_level = os.environ.get('SCHEDULER_LOG_LEVEL', 'INFO').upper()
logger_discord.setLevel(log_level)

database = {}

rec_path = "/recordings"

# Bot functions
@bot.event
async def on_ready():
    # Schedule a database update
    scheduler.add_job(func=query_database, trigger='interval', seconds=30, id='query_database') 
    scheduler.start()
    scheduler.print_jobs()
      
@bot.command(name='hello', help='Say hello to the bot')
@has_role(worshipper_role_name)
async def hello(ctx):
    author_name = ctx.author.name
    await ctx.channel.send(f'Hi, {author_name} :blush:')

@hello.error
async def hello_error(ctx, error):
    if isinstance(error, CheckFailure):
        await ctx.channel.send('Do I know you?')

@bot.command(name='time', help='Show current time in UTC')
async def time(ctx):
    await ctx.channel.send(f'The Coordinated Universal Time is `{datetime.now(timezone.utc)}`')

@bot.command(name='epg', help='Lists scheduled streams')    
async def epg(ctx):
    global database
    if database != {}:
        scheduled_list = ""
        live_list = ""
        for key, value in database.items():
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
    
@bot.command(name='now', help='Displays whats playing right now')
async def now(ctx):
    playhead = await query_playhead()
    stream_name = playhead['name']
    stream_prio = playhead['prio']
    await ctx.channel.send(f'Now playing {stream_name} (prio={stream_prio})')

@bot.command(name='rec', help='Start the recorder')
@has_role(boss_role_name)
async def rec(ctx):
    playhead = await query_playhead()
    stream_name = playhead['name']
    stream_prio = playhead['prio']
    # Check if the recorder job already exists
    if scheduler.get_job('recorder') is None:
        await ctx.channel.send(f'Recording from {stream_name} (prio={stream_prio})')
        scheduler.add_job(func=exec_recorder, id='recorder', args=(playhead))
        scheduler.print_jobs()
    else:
        await ctx.channel.send(f'Recorder is busy!')

@rec.error
async def rec_error(ctx, error):
    if isinstance(error, CheckFailure):
        await ctx.channel.send('Access denied!')

@bot.command(name='stop', help='Stop the recorder')
@has_role(boss_role_name)
async def stop(ctx):
    # Check if the recorder job already exists
    if scheduler.get_job('recorder') is not None:
        await ctx.channel.send(f'Shutting down recorder')
        scheduler.remove_job('recorder')
        scheduler.print_jobs()
    else:
        await ctx.channel.send(f'Recorder is stopped.')

@stop.error
async def stop_error(ctx, error):
    if isinstance(error, CheckFailure):
        await ctx.channel.send('Access denied!')

async def query_playhead():
    head_url = f'https://{scheduler_hostname}/playhead'
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(head_url)
            playhead = response.json()
            logger_discord.info(f'Playhead at {playhead['head']}') 
    except Exception as e:
        logger_discord.error('Cannot connect to the playhead!')
        logger_discord.error(e)
        await asyncio.sleep(5)
    return playhead

async def query_database():
    global database
    db_url = f'https://{scheduler_hostname}/database'
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(db_url)
            database = response.json()
            logger_discord.info(f'Database {database.keys()}') 
    except Exception as e:
        logger_discord.error('Cannot connect to the database!')
        logger_discord.error(e)
        await asyncio.sleep(5)
        return
        
    if database == {}:
        logger_discord.error('Database is empty!')
        return
    
    # Search for live streams and announce them
    for key, value in database.items():
        stream_name = value['name']
        stream_start_at = value['start_at']
        stream_meta = value['meta']
        if stream_start_at == 'now':
            # Check if the announement job already exists
            if scheduler.get_job('announce_live_channel') is None:
                # Job doesn't exist, so add it
                logger_discord.info(f'{stream_name} live stream detected!')
                scheduler.add_job(func=announce_live_channel, trigger='interval', minutes=int(live_channel_update), id='announce_live_channel', args=(stream_name, stream_meta))
                scheduler.get_job('announce_live_channel').modify(next_run_time=datetime.now())
                scheduler.print_jobs()
                return
            else:
                # Exit since we already have a live announcement job
                return

    # Cleanup the announce job
    if scheduler.get_job('announce_live_channel') is not None:
        scheduler.remove_job('announce_live_channel')
        scheduler.print_jobs()
        if live_channel_id != 0:
            live_channel = bot.get_channel(int(live_channel_id))
            await live_channel.send(f'{stream_name} is offline.')
        logger_discord.info(f'{stream_name} is offline.')

async def announce_live_channel(stream_name, stream_meta):
    if live_channel_id != 0:
        live_channel = bot.get_channel(int(live_channel_id))
        await live_channel.send(f'{stream_name} is live! :satellite_orbital: {stream_meta}')
    logger_discord.info(f'{stream_name} is live! {stream_meta}')

# Execute recorder
async def exec_recorder(playhead):
    stream_id = playhead['id']
    stream_name = playhead['name']
    stream_hls_url = playhead['head']
    current_datetime = datetime.now().strftime("%Y%m%d_%H%M%S")
    video_file = current_datetime + ".mp4"
    thumb_file = current_datetime + ".png"
    video_output = f'{rec_path}/live/{video_file}'
    thumb_output = f'{rec_path}/live/{thumb_file}'
        
    try:
        logger_discord.info(f'Recording video {video_file}')
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
            print(progress)
        ffmpeg.execute()
        logger_discord.info(f'Recording of {video_file} finished.')

    except Exception as joberror:
        logger_discord.error(f'Recording of {video_file} failed!')
        logger_discord.error(joberror)

    else:
        # Show Metadata
        ffmpeg_metadata = (
            FFmpeg(executable="ffprobe")
            .input(video_output,
                   print_format="json",
                   show_streams=None,)
        )
        media = json.loads(ffmpeg_metadata.execute())
        logger_discord.info(f"# Video")
        logger_discord.info(f"- Codec: {media['streams'][0]['codec_name']}")
        logger_discord.info(f"- Resolution: {media['streams'][0]['width']} X {media['streams'][0]['height']}")
        logger_discord.info(f"- Duration: {media['streams'][0]['duration']}")
        logger_discord.info(f"# Audio")
        logger_discord.info(f"- Codec: {media['streams'][1]['codec_name']}")
        logger_discord.info(f"- Sample Rate: {media['streams'][1]['sample_rate']}")
        logger_discord.info(f"- Duration: {media['streams'][1]['duration']}")
        
        thumb_skip_time = float(media['streams'][0]['duration']) // 2
        thumb_width = media['streams'][0]['width'] 
    
    try:
        logger_discord.info(f'Generating thumb {thumb_file}')
        # Generate thumbnail image from the recorded mp4 file
        ffmpeg_thumb = (
            FFmpeg()
            .input(video_output, ss=thumb_skip_time)
            .output(thumb_output, vf='scale={}:{}'.format(thumb_width, -1), vframes=1)
        )
        ffmpeg_thumb.execute()
        logger_discord.info(f'Thumbnail {thumb_file} created.')
    
    except Exception as joberror:
        logger_discord.error(f'Generating thumb {thumb_file} failed!')
        logger_discord.error(joberror)

    # When ready, move the recorded from the live dir to the archives and reset the rec head
    os.rename(f'{video_output}', f'{rec_path}/vod/{video_file}')
    os.rename(f'{thumb_output}', f'{rec_path}/thumb/{thumb_file}')
    await create_embed(stream_name, video_output, thumb_output)
    logger_discord.info('Recording job done in {rec_job_time} minutes')

# HLS Converter
async def hls_converter():
    directory = f'{rec_path}/vod/'
    try:
        # Check if the directory exists
        if not os.path.exists(directory):
            raise FileNotFoundError(f"The directory '{directory}' does not exist.")
            
        # Iterate through all entries in the directory
        for entry in os.listdir(directory):
            file_path = os.path.join(directory, entry)
            if entry.lower().endswith('.mp4'):
                input_file = file_path
                break
        #logger_discord.info(f'{input_file} found. Converting to HLS...')
    except Exception as e:
        logger_discord.error(e)

async def create_embed(stream_name, video_filename, thumb_filename):
    # Creating an embed
    img_url = f'https://{scheduler_hostname}/static/images'
    thumb_url = f'https://{scheduler_hostname}/thumb/{thumb_filename}'
    video_download_url = f'https://{scheduler_hostname}/video/download/{video_filename}'
    video_filename_no_extension = video_filename.split('.')[0]
    video_watch_url = f'https://{scheduler_hostname}/video/watch/{video_filename_no_extension}'
    embed = discord.Embed(title=f'VOD: {video_filename_no_extension}',
                          url=f'{video_watch_url}',
                          description=f'{stream_name}',
                          colour=0x00b0f4,
                          timestamp=datetime.now())               
    embed.add_field(name="Download",
                    value=f'[mp4 file]({video_download_url})',
                    inline=True)
    embed.add_field(name="Watch",
                    value=f'[plyr.js player]({video_watch_url}) :]',
                    inline=True)
    embed.set_image(url=thumb_url)
    #embed.set_thumbnail(url=f'{img_url}/logo-96.png')
    embed.set_footer(text="DeflaxTV", 
                    icon_url=f'{img_url}/logo-96.png')
    await live_channel.send(embed=embed)

# Run the bot with your token
asyncio.run(bot.run(bot_token))