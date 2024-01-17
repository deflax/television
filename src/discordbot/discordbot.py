import asyncio
import os
import requests
import discord
from discord.ext import commands, tasks
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.base import JobLookupError
import logging

# Read env variables
bot_token = os.environ.get('DISCORDBOT_TOKEN', 'token')
announce_channel_id = os.environ.get('DISCORDBOT_LIVE_CHANNEL_ID', 'disabled')
scheduler_hostname = os.environ.get('SCHEDULER_API_HOSTNAME', 'tv.example.com')

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

# Bot functions
@bot.event
async def on_ready():
    # Schedule a database update to run every 5 seconds
    scheduler.add_job(func=update_database, trigger='interval', seconds=5, id='update_database') 
    scheduler.start()
      
@bot.command(name='hello')
async def hello(ctx):
    author_name = ctx.author.name
    await ctx.channel.send(f'hi, {author_name}! >^.^<')

@bot.command(name='epg')    
async def epg(ctx):
    global database
    await ctx.channel.send('epg:')
    if database != {}:
        epg_list = ""
        for key, value in database.items():
            item_name = value['name']
            item_start = value['start_at']
            if item_start != 'now' and item_start != 'never':
                epg_list += f'{item_name} starts at {item_start}h UTC\n')
        await ctx.channel.send(f'``` {epg_list} ```')
    else:
        await ctx.channel.send('Empty database!')
 
@bot.command(name='time')
async def time(ctx):
    await ctx.channel.send(f'The time is: {datetime.now()} UTC')

# Helper functions
async def update_database():
    global database
    db_url = f'https://{scheduler_hostname}/database'
    if requests.get(db_url).status_code == 200:
        response = requests.get(db_url)
        response.raise_for_status()
        database = response.json()
    else:
        logger_discord.error('Cannot connect to the database!')
        return
        
    # Search for live streams
    if database != {}:
        for key, value in database.items():
            stream_name = value['name']
            stream_start_at = value['start_at']       
            if stream_start_at == 'now':
                # Check if the job already exists
                if scheduler.get_job('announce_live_channel') is None:
                    # Job doesn't exist, so add it
                    logger_discord.info(f'{stream_name} live stream detected!')
                    scheduler.add_job(func=announce_live_channel, trigger='interval', seconds=60, id='announce_live_channel', args=(stream_name,))
                    # Manually execute the job once immediately
                    scheduler.get_job('announce_live_channel').modify(next_run_time=datetime.now())
                    # Exit since we found one
                    return
                else:
                    # Exit since we already have a announcement job
                    return

        # Cleanup the announce job
        if scheduler.get_job('announce_live_channel') is not None:
            scheduler.remove_job('announce_live_channel')
            #live_channel = bot.get_channel(announce_channel_id)
            logger_discord.info(f'Live stream is offline.')
            #await live_channel.send('Live stream is offline.')

async def announce_live_channel(stream_name):
    if announce_channel_id == 'disabled':
        return
    else:
        #live_channel = bot.get_channel(announce_channel_id)
        logger_discord.info(f'{stream_name} is live!')
        #await live_channel.send(f'{stream_name} is live!')

# Run the bot with your token
asyncio.run(bot.run(bot_token))