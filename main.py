from threading import Thread
import os
from dotenv import load_dotenv
import nextcord
from nextcord.ext import commands

# Load environment variables from .env
load_dotenv()

# Get the Discord bot token from .env
DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')

# Setup intents and create the bot instance
intents = nextcord.Intents.default()
intents.message_content = True  # Allows the bot to read message content
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    # Force sync slash commands across guilds
    await bot.sync_application_commands()  
    print(f'Bot logged in as {bot.user}')

# Load Cogs (extensions)
if __name__ == "__main__":
    initial_extensions = ['cogs.chat', 'cogs.music', 'cogs.dalle', 'cogs.localization', 'cogs.help', 'cogs.f1']
    for extension in initial_extensions:
        try:
            bot.load_extension(extension)
            print(f"Loaded {extension} successfully")
        except Exception as e:
            print(f"Failed to load extension {extension}: {type(e).__name__} - {e}")

# Import the Flask app from web/app.py
from web.app import app as flask_app

def run_flask():
    # Start the Flask web server on port 8080
    flask_app.run(host="0.0.0.0", port=8080)

if __name__ == "__main__":
    # Start the Flask app in a background daemon thread
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # Now start the Discord bot
    bot.run(DISCORD_BOT_TOKEN)













