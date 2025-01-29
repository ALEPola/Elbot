import os
from dotenv import load_dotenv
import nextcord
from nextcord.ext import commands

# Load environment variables from .env
load_dotenv()

# Get the bot token from .env
DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')

# Set up intents and bot
intents = nextcord.Intents.default()
intents.message_content = True  # Enables bot to read message content, which is necessary for processing commands and responding to messages

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    # This will force sync the slash commands across your guilds
    await bot.sync_application_commands()  # Sync all slash commands, this might take some time and should be used cautiously in large bots
    print(f'We have logged in as {bot.user}')

# Load Cogs
if __name__ == "__main__":
    initial_extensions = ['cogs.chat', 'cogs.music', 'cogs.dalle']  # Add your cogs here

    for extension in initial_extensions:
        try:
            bot.load_extension(extension)
            print(f"Loaded {extension} successfully")
        except Exception as e:
            print(f"Failed to load extension {extension}: {type(e).__name__} - {e}")

# Run the bot
bot.run(DISCORD_BOT_TOKEN)














