import os
import discord

# Read env variables
bot_token = os.environ.get('DISCORDBOT_TOKEN', 'token')
scheduler_hostname = os.environ.get('SCHEDULER_API_HOSTNAME', 'tv.example.com')

# Intents
intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)

# Define an event when the bot is ready
@client.event
async def on_ready():
    print(f'Logged in as {client.user}')

async def hello(message):
    await message.channel.send('Hello!')

@client.event
async def on_message(message):
    if message.author == client.user:
        return

    if message.content.startswith('!hello'):
        await hello(message)

client.run(bot_token)