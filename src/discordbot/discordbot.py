import asyncio
import os
import requests
import discord
from discord.ext import commands, tasks
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler

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
        for key, value in database.items():
            if value['start_at'] != 'now' and value['start_at'] != 'never':
                await ctx.channel.send(f'{value['name']} starts at {value['start_at']}h UTC')
    else:
        await ctx.channel.send('Empty database!')
 
@bot.command(name='time')
async def time(ctx):
    await ctx.channel.send('The time is: `%s`' % datetime.now())

# Helper functions
async def update_database():
    global database
    db_url = f'https://{scheduler_hostname}/database'
    if requests.get(db_url).status_code == 200:
        response = requests.get(db_url)
        response.raise_for_status()
        database = response.json()
        if database != {}:
            for key, value in database.items():
                if value['start_at'] == 'now':
                    scheduler.add_job(func=announce_live_channel, seconds=60, id='announce_live_channel') 
                    

async def announce_live_channel():
    if announce_channel_id == 'disabled':
        return
    else:
        live_channel = bot.get_channel(announce_channel_id)
        await live_channel.send(f'{announce_channel_id}')

# Run the bot with your token
asyncio.run(bot.run(bot_token))
