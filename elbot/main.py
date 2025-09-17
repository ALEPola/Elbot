# elbot/main.py

import os
import sys
import logging
from pathlib import Path
from logging.handlers import RotatingFileHandler
import nextcord
from nextcord.ext import commands
os.environ.setdefault("MAFIC_IGNORE_LIBRARY_CHECK", "1")
import mafic
from dotenv import load_dotenv
from .config import Config
from .utils import load_all_cogs

# ── Logging Setup ────────────────────────────────────────────────────────────
logger = logging.getLogger("elbot")
logger.setLevel(logging.INFO)

logger.handlers.clear()

log_path = Path(Config.BASE_DIR) / "logs" / "elbot.log"
log_path.parent.mkdir(exist_ok=True)
file_handler = RotatingFileHandler(
    log_path, maxBytes=10 * 1024 * 1024, backupCount=3
)
file_formatter = logging.Formatter("%(asctime)s %(levelname)s: %(message)s")
file_handler.setFormatter(file_formatter)
logger.addHandler(file_handler)

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(file_formatter)
logger.addHandler(console_handler)


def main():
    load_dotenv()
    # 0) Optionally start a local Lavalink instance
    if os.getenv("AUTO_LAVALINK", "1") == "1":
        try:
            from elbot.auto_lavalink import start as start_lavalink

            port, pw = start_lavalink()
            print(f"[bot] Auto-Lavalink: 127.0.0.1:{port}")
        except Exception as e:
            print(f"[bot] Auto-Lavalink failed: {e}")

    # 1) Verify required environment variables
    Config.validate()

    # 2) Create bot with intents
    intents = nextcord.Intents.default()
    intents.message_content = True
    intents.voice_states = True
    bot = commands.Bot(command_prefix=Config.PREFIX, intents=intents)

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

    @bot.slash_command(name="musicdebug", description="Show Lavalink status")
    async def musicdebug(inter: nextcord.Interaction):
        nodes = mafic.NodePool.label_to_node
        if not nodes:
            status = "No Lavalink nodes are connected."
        else:
            node = next(iter(nodes.values()))
            status = (
                f"{node.label} available={node.available} "
                f"players={len(node.players)}"
            )
        await inter.response.send_message(status, ephemeral=True)

    # 6) Run the bot
    try:
        bot.run(Config.DISCORD_TOKEN)
    except Exception as e:
        logger.exception("Bot failed to start:")
        raise e


if __name__ == "__main__":
    main()
