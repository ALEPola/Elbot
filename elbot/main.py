# elbot/main.py

import sys
import logging
import asyncio
import nextcord
from nextcord.ext import commands
from .config import Config
from .utils import load_all_cogs

# ── Logging Setup ────────────────────────────────────────────────────────────
logger = logging.getLogger("elbot")
logger.setLevel(logging.INFO)

# File handler (writes to elbot.log)
file_handler = logging.FileHandler("elbot.log")
file_formatter = logging.Formatter("%(asctime)s %(levelname)s: %(message)s")
file_handler.setFormatter(file_formatter)
logger.addHandler(file_handler)

# Console handler
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(file_formatter)
logger.addHandler(console_handler)


def main():
    # 1) Verify required environment variables
    Config.validate()

    # 2) Create bot with intents
    intents = nextcord.Intents.default()
    intents.message_content = True

    # Create and set event loop before instantiating the bot
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    bot = commands.Bot(command_prefix=Config.PREFIX, intents=intents, loop=loop)

    # 3) Global error handler
    @bot.event
    async def on_command_error(ctx, error):
        if isinstance(error, commands.CommandNotFound):
            await ctx.send(f"❌ Command not found. Use `{Config.PREFIX}help`.")
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("❌ Missing required argument.")
        elif isinstance(error, commands.CommandOnCooldown):
            await ctx.send(
                f"⏳ Command on cooldown. Try in {round(error.retry_after, 2)}s."
            )
        else:
            logger.error(f"Unhandled error: {error}", exc_info=True)
            await ctx.send("❌ An unexpected error occurred. Contact the admin.")

    # 4) on_ready logging
    @bot.event
    async def on_ready():
        logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
        print(f"ℹ️  Bot is ready as {bot.user}")

    # 5) Load every cog in the cogs/ directory
    load_all_cogs(bot, cogs_dir="cogs")

    # 6) Run the bot
    try:
        bot.run(Config.DISCORD_TOKEN)
    except Exception as e:
        logger.exception("Bot failed to start:")
        raise e


if __name__ == "__main__":
    main()
