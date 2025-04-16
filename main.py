from threading import Thread
import os
from dotenv import load_dotenv
import nextcord
from nextcord.ext import commands

# 1) Load .env first
load_dotenv()
DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
GUILD_ID          = int(os.getenv('GUILD_ID',   '0'))
# …any other env vars…

# 2) Create the bot
intents = nextcord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    # 3) Sync *guild* commands for instant availability
    await bot.sync_application_commands(guild_id=GUILD_ID)
    print(f'✅ Logged in as {bot.user}')

def run_flask():
    # 4) Disable the reloader so you don’t spin up two processes
    from web.app import app as flask_app
    flask_app.run(host="0.0.0.0", port=8080, debug=False, use_reloader=False)

if __name__ == "__main__":
    # 5) Load your Cogs
    for ext in ('cogs.chat','cogs.music','cogs.dalle','cogs.localization','cogs.help','cogs.f1'):
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












