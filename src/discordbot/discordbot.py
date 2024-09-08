import asyncio
import os
import requests
import discord
from discord.ext import commands, tasks
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import logging

# Read env variables
bot_token = os.environ.get('DISCORDBOT_TOKEN', 'token')
live_channel_id = os.environ.get('DISCORDBOT_LIVE_CHANNEL_ID', 0)
live_channel_update = os.environ.get('DISCORDBOT_LIVE_CHANNEL_UPDATE', 1440)
scheduler_hostname = os.environ.get('SCHEDULER_API_HOSTNAME', 'api.example.com')

# Discord API Intents
intents = discord.Intents.all()
intents.members = True
intents.guilds = True
intents.messages = True
intents.reactions = True
intents.presences = True
intents.message_content = True

# Discord client
bot = commands.Bot(command_prefix="!", intents=intents)

# Scheduler
scheduler = AsyncIOScheduler()

# Set up the logger
logger_discord = logging.getLogger('discord')
log_level = os.environ.get('SCHEDULER_LOG_LEVEL', 'INFO').upper()
logger_discord.setLevel(log_level)

database = {}
rechead = {}

# Bot functions
@bot.event
async def on_ready():
    # Schedule a database update to run every 5 seconds
    scheduler.add_job(func=query_database, trigger='interval', seconds=5, id='query_database') 
    scheduler.start()
      
@bot.command(name='hello', help='Say hello to the bot')
async def hello(ctx):
    author_name = ctx.author.name
    await ctx.channel.send(f'hi, `{author_name}` :blush:')

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
 
@bot.command(name='time', help='Show current time')
async def time(ctx):
    await ctx.channel.send(f'The time is: `{datetime.now()} UTC`')
    
@bot.command(name='now', help='Displays whats playing right now')
async def now(ctx):
    head = await query_playhead()
    await ctx.channel.send(head)

# Helper functions
async def query_playhead():
    head_url = f'https://{scheduler_hostname}/playhead'
    if requests.get(head_url).status_code == 200:
        response = requests.get(head_url)
        response.raise_for_status()
        playhead = response.json()
    else:
        logger_discord.error('Cannot connect to the playhead!')
    head_name = playhead['name']
    head_prio = playhead['prio']
    return f'Now playing {head_name}'

async def query_database():
    global database
    global rechead
    db_url = f'https://{scheduler_hostname}/database'
    try:
        if requests.get(db_url).status_code == 200:
            response = requests.get(db_url)
            response.raise_for_status()
            database = response.json()
        else:
            logger_discord.error('Cannot connect to the database!')
            return
    except Exception as e:
        logger_discord.error('Cannot connect to the database!')
        logger_discord.error(e)
        await asyncio.sleep(5)
        return
        
    if database == {}:
        logger_discord.error('Database is empty!')
        return
    
    # Search for live streams
    for key, value in database.items():
        stream_name = value['name']
        stream_start_at = value['start_at']
        stream_meta = value['meta']
        if stream_start_at == 'now':
            # Check if the job already exists
            if scheduler.get_job('announce_live_channel') is None:
                # Job doesn't exist, so add it
                logger_discord.info(f'{stream_name} live stream detected!')
                scheduler.add_job(func=announce_live_channel, trigger='interval', minutes=int(live_channel_update), id='announce_live_channel', args=(stream_name, stream_meta))
                
                # Manually execute the job once immediately
                scheduler.get_job('announce_live_channel').modify(next_run_time=datetime.now())
                
                # Set global rechead
                rec_url = f'https://{scheduler_hostname}/rechead'
                if requests.get(rec_url).status_code == 200:
                    response = requests.get(rec_url)
                    response.raise_for_status()
                    rechead = response.json()
                
                # Exit since we found one
                return
            else:
                # Exit since we already have a live announcement job
                return

    # Cleanup the announce job
    if scheduler.get_job('announce_live_channel') is not None:
        scheduler.remove_job('announce_live_channel')
        if live_channel_id != 0:
            live_channel = bot.get_channel(int(live_channel_id))
            if rechead != {}:
                rec_stream_name = rechead['name']
                video_filename = rechead['video']
                thumb_filename = rechead['thumb']
                # Reset the rechead
                rechead = {}
                               
                # Creating an embed
                img_url = f'https://{scheduler_hostname}/img'
                thumb_url = f'https://{scheduler_hostname}/thumb/{thumb_filename}'
                video_download_url = f'https://{scheduler_hostname}/video/download/{video_filename}'
                video_filename_no_extension = video_filename.split('.')[0]
                video_watch_url = f'https://{scheduler_hostname}/video/watch/{video_filename_no_extension}'
                embed = discord.Embed(title=f'VOD: {video_filename_no_extension}',
                                      url=f'{video_watch_url}',
                                      description=f'{rec_stream_name}',
                                      colour=0x00b0f4,
                                      timestamp=datetime.now())               
                embed.add_field(name="Download",
                                value=f'[mp4 file]({video_download_url})',
                                inline=True)
                embed.add_field(name="Watch",
                                value=f'[plyr.js player]({video_watch_url}) :]',
                                inline=True)
                #embed.set_image(url=thumb_url)
                #embed.set_thumbnail(url=f'{img_url}/logo-96.png')
                embed.set_footer(text="DeflaxTV", 
                                 icon_url=f'{img_url}/logo-96.png')
                # Sending the embed to the channel
                await live_channel.send(embed=embed)
                logger_discord.info(f'{rec_stream_name} is offline. VOD: {video_filename_no_extension}')
            else:
                # Send offline message only
                await live_channel.send('Stream is offline.')
        logger_discord.info('Stream is offline.')

async def announce_live_channel(stream_name, stream_meta):
    logger_discord.info(f'{stream_name} is live! {stream_meta}')
    if live_channel_id != 0:
        live_channel = bot.get_channel(int(live_channel_id))
        await live_channel.send(f'{stream_name} is live! :satellite_orbital: {stream_meta}')

# Run the bot with your token
asyncio.run(bot.run(bot_token))
