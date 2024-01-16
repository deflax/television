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

counter = 0

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
            content = response.json()
            await ctx.channel.send('epg:')
            
            if content != {}:
                for key, value in content.items():
                    if value['start_at'] == 'now' or value['start_at'] == 'never':
                        await ctx.channel.send('x')
                        continue
                    else:
                        await ctx.channel.send('x')
                        await ctx.channel.send(value['start_at'])
            else:
                await ctx.channel.send('Empty database!')
    except Exception as e:
        print(e)

@bot.command(name='time')
async def time(ctx):
    await ctx.channel.send('The time is: `%s`' % datetime.now())

@bot.command(name='start')
async def start_task(ctx):
    # Schedule a task to run every 5 seconds
    scheduler.add_job(func=my_task, id='my_task_id')
    #scheduler.add_job(func=tick, id='tick_id', args=(ctx))
    #channel = bot.get_channel(channel_id)
    #if channel:
    #    # Send the message to the specified channel
    #    await channel.send(message)

@bot.command(name='show')
async def show_task(ctx):
    global counter
    await ctx.channel.send(str(counter))

@tasks.loop(seconds=10)
async def my_task():
    global counter
    counter += 1

# Run the bot with your token
bot.run(bot_token)
