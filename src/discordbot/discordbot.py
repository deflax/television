import asyncio
import os
import requests
import discord
from discord.ext import commands, tasks
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Read env variables
bot_token = os.environ.get('DISCORDBOT_TOKEN', 'token')
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

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name} ({bot.user.id})')
    scheduler.start()
      
@bot.command(name='hello')
async def hello(ctx):
    await ctx.channel.send('Hello!')

@bot.command(name='epg')    
async def epg(ctx):
    try:
        db_url = f'https://{scheduler_hostname}/database'
        if requests.get(db_url).status_code == 200:
            response = requests.get(db_url)
            response.raise_for_status()

            content = response.text
            await ctx.channel.send(content)
    except Exception as e:
        print(e)

@bot.command(name='start_task')
async def start_task(ctx):
    # Schedule a task to run every 5 seconds
    scheduler.add_job(func=my_task, seconds=5, id='my_task_id', args=(ctx))
    #scheduler.add_job(func=tick, seconds=5, id='tick_id', args=(ctx))

@tasks.loop(seconds=10)
async def my_task(ctx):
    # Your asynchronous task goes here
    print()
    await ctx.channel.send('Running my_task')

async def tick(ctx):
    await ctx.channel.send('Tick! The time is: %s' % datetime.now())

# Run the bot with your token
bot.run(bot_token)
