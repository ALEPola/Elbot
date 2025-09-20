# cogs/diagnostic.py

import platform
import psutil
import logging
import time

import nextcord
from nextcord.ext import commands

from elbot.utils import safe_reply

logger = logging.getLogger("elbot.diagnostic")


class DiagnosticCog(commands.Cog):
    """
    A cog that provides basic bot and system diagnostics.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.start_time = time.time()

    @nextcord.slash_command(name="uptime", description="Check the bot's uptime.")
    async def uptime(self, interaction: nextcord.Interaction):
        """
        Report how long the bot has been running.
        """
        await interaction.response.defer(with_message=True)
        now = time.time()
        uptime_seconds = now - self.start_time
        hours, remainder = divmod(int(uptime_seconds), 3600)
        minutes, seconds = divmod(remainder, 60)
        uptime_str = f"{hours}h {minutes}m {seconds}s"
        await safe_reply(interaction, f"🕒 Uptime: {uptime_str}")

    @nextcord.slash_command(name="ping", description="Check the bot's latency.")
    async def ping(self, interaction: nextcord.Interaction):
        """
        Return the current bot latency in milliseconds.
        """
        await interaction.response.defer(with_message=True)
        latency_ms = round(self.bot.latency * 1000)
        await safe_reply(interaction, f"🏓 Latency: {latency_ms}ms")

    @nextcord.slash_command(name="cogs", description="List all loaded cogs.")
    async def cogs(self, interaction: nextcord.Interaction):
        """
        Show which cogs are currently loaded.
        """
        await interaction.response.defer(with_message=True)
        loaded = list(self.bot.cogs.keys())
        if not loaded:
            await safe_reply(interaction, "No cogs are currently loaded.")
        else:
            cog_list = "\n".join(f"- {name}" for name in loaded)
            await safe_reply(interaction, f"📂 Loaded Cogs:\n{cog_list}")

    @nextcord.slash_command(
        name="system_info", description="Get basic system information."
    )
    async def system_info(self, interaction: nextcord.Interaction):
        """
        Report OS, CPU, and total RAM.
        """
        await interaction.response.defer(with_message=True)
        system = platform.system()
        release = platform.release()
        cpu = platform.processor()
        total_ram = psutil.virtual_memory().total / (1024**3)  # Convert bytes to GB
        await safe_reply(
            interaction,
            f"🖥 **System Information:**\n"
            f"- OS: {system} {release}\n"
            f"- CPU: {cpu}\n"
            f"- Memory: {total_ram:.2f} GB",
        )


def setup(bot: commands.Bot):
    bot.add_cog(DiagnosticCog(bot))
    logger.info("✅ Loaded DiagnosticCog")
