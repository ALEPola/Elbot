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
intents.message_content = True  # Needed for regular commands and reading message content

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    # Force sync the slash commands across your guilds
    synced = await bot.sync_application_commands(force=True)
    print(f"Synced {len(synced)} slash commands.")
    print(f"We have logged in as {bot.user}")

# Load Cogs (make sure each cog is listed as a separate string)
if __name__ == "__main__":
    initial_extensions = [
        'cogs.chat',      # If you use a chat cog
        'cogs.music',
        'cogs.dalle',
        'cogs.localization',
        'cogs.help',
        'cogs.f1',        # Our Formula One cog
        'cogs.pingtest'   # Your other cog (if any)
    ]

    for extension in initial_extensions:
        try:
            bot.load_extension(extension)
            print(f"Loaded {extension} successfully")
        except Exception as e:
            print(f"Failed to load extension {extension}: {type(e).__name__} - {e}")

# Run the bot
bot.run(DISCORD_BOT_TOKEN)











