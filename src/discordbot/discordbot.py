import asyncio
import os
import discord
import requests
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Read env variables
bot_token = os.environ.get('DISCORDBOT_TOKEN', 'token')
scheduler_hostname = os.environ.get('SCHEDULER_API_HOSTNAME', 'tv.example.com')

# Scheduler
scheduler = AsyncIOScheduler()

# Intents
intents = discord.Intents.default()
intents.message_content = True

# Client
client = discord.Client(intents=intents)

# Define an event when the bot is ready
@client.event
async def on_ready():
    print(f'Logged in as {client.user}')

async def hello(message):
    await message.channel.send('Hello!')

async def epg(message):
    try:
        db_url = f'https://{scheduler_hostname}/database'
        if requests.get(db_url).status_code == 200:
            response = requests.get(db_url)
            response.raise_for_status()

            content = response.text
            await message.channel.send(content)
    except Exception as e:
        print(e)

@client.event
async def on_message(message):
    if message.author == client.user:
        return

    if message.content.startswith('!hello'):
        await hello(message)
        
    if message.content.startswith('!epg'):
        await epg(message)

client.run(bot_token)

def tick():
    print('Tick! The time is: %s' % datetime.now())

scheduler.add_job(tick, 'interval', seconds=3)
scheduler.start()

print('Press Ctrl+{0} to exit'.format('Break' if os.name == 'nt' else 'C'))

# Execution will block here until Ctrl+C (Ctrl+Break on Windows) is pressed.
try:
    asyncio.get_event_loop().run_forever()
except (KeyboardInterrupt, SystemExit):
    pass

