import os
import discord
from discord.ext import commands

# Read env variables
bot_token = os.environ.get('DISCORDBOT_TOKEN', 'token')
scheduler_hostname = os.environ.get('SCHEDULER_API_HOSTNAME', 'tv.example.com')

# Create a bot instance with a command prefix
bot = commands.Bot(command_prefix='!')

# Define a simple command
@bot.command(name='about', help='Displays about info')
async def hello(ctx):
    await ctx.send(f'Hello {ctx.author.name}! ')

# Define an event when the bot is ready
@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')

# Run the bot with your bot token
bot.run(bot_token)