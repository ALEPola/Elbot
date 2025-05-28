from threading import Thread
import os
from dotenv import load_dotenv
import nextcord
from nextcord.ext import commands
import logging
import sys
import atexit
import tempfile

# 1) Load .env first
load_dotenv()  # Load environment variables from .env file
DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
GUILD_ID          = int(os.getenv('GUILD_ID',   '0'))
# …any other env vars…

# 2) Create the bot
intents = nextcord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("elbot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("ELBOT")

LOCK_FILE = os.path.join(tempfile.gettempdir(), 'elbot.lock')

def ensure_single_instance():
    try:
        if os.path.exists(LOCK_FILE):
            with open(LOCK_FILE, 'r') as lock:
                pid = int(lock.read().strip())
                # Check if process with stored PID is still running
                try:
                    os.kill(pid, 0)  # Doesn't kill the process, just checks if it exists
                    print(f"Another instance of the bot is already running (PID: {pid}).")
                    sys.exit(1)
                except OSError:  # Process is not running
                    os.remove(LOCK_FILE)  # Clean up stale lock file
                    
        with open(LOCK_FILE, 'w') as lock:
            lock.write(str(os.getpid()))

        def cleanup():
            if os.path.exists(LOCK_FILE):
                os.remove(LOCK_FILE)

        atexit.register(cleanup)
    except Exception as e:
        logger.error(f"Error managing lock file: {e}")
        # Continue running even if lock file management fails
        pass

# Ensure only one instance runs
ensure_single_instance()

@bot.event
async def on_ready():
    if GUILD_ID == 0:
        logger.warning("GUILD_ID is not set or invalid. Skipping command sync.")
    else:
        try:
            # 3) Sync *guild* commands for instant availability
            await bot.sync_application_commands(guild_id=GUILD_ID)
            print(f'✅ Logged in as {bot.user}')
        except Exception as e:
            logger.error(f"Failed to sync commands: {e}", exc_info=True)

# Global error handler
@bot.event
async def on_command_error(ctx, error):
    """Handle errors globally for commands."""
    if isinstance(error, commands.CommandNotFound):
        await ctx.send("❌ Command not found. Use `!help` to see available commands.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❌ Missing required argument. Please check the command usage.")
    elif isinstance(error, commands.CommandOnCooldown):
        await ctx.send(f"⏳ This command is on cooldown. Try again in {round(error.retry_after, 2)} seconds.")
    else:
        logger.error(f"Unhandled error: {error}", exc_info=True)
        await ctx.send("❌ An unexpected error occurred. Please contact the admin.")

def run_flask():
    # 4) Disable the reloader so you don’t spin up two processes
    from web.app import app as flask_app
    flask_app.run(host="0.0.0.0", port=8081, debug=False, use_reloader=False)

if __name__ == "__main__":
    # 5) Load your Cogs
    for ext in ('cogs.chat','cogs.music','cogs.dalle','cogs.localization','cogs.help','cogs.f1','cogs.pingptest'):
        try:
            bot.load_extension(ext)
            print(f'Loaded {ext}')
        except Exception as e:
            print(f'Failed to load {ext}:', e)

    # 6) Spin up Flask in a daemon thread
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # 7) Finally, start your bot
    bot.run(DISCORD_BOT_TOKEN)












