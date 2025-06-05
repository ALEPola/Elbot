# cogs/diagnostic.py

import platform
import psutil
import logging

import nextcord
from nextcord.ext import commands

logger = logging.getLogger("elbot.diagnostic")

class DiagnosticCog(commands.Cog):
    """
    A cog that provides basic bot and system diagnostics.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.start_time = psutil.boot_time()  # or store datetime.now() if you prefer

    @nextcord.slash_command(
        name="uptime",
        description="Check the bot's uptime."
    )
    async def uptime(self, interaction: nextcord.Interaction):
        """
        Report how long the bot has been running.
        """
        now = psutil.time.time()
        uptime_seconds = now - self.start_time
        hours, remainder = divmod(int(uptime_seconds), 3600)
        minutes, seconds = divmod(remainder, 60)
        uptime_str = f"{hours}h {minutes}m {seconds}s"
        await interaction.response.send_message(f"🕒 Uptime: {uptime_str}")

    @nextcord.slash_command(
        name="ping",
        description="Check the bot's latency."
    )
    async def ping(self, interaction: nextcord.Interaction):
        """
        Return the current bot latency in milliseconds.
        """
        latency_ms = round(self.bot.latency * 1000)
        await interaction.response.send_message(f"🏓 Latency: {latency_ms}ms")

    @nextcord.slash_command(
        name="cogs",
        description="List all loaded cogs."
    )
    async def cogs(self, interaction: nextcord.Interaction):
        """
        Show which cogs are currently loaded.
        """
        loaded = list(self.bot.cogs.keys())
        if not loaded:
            await interaction.response.send_message("No cogs are currently loaded.")
        else:
            cog_list = "\n".join(f"- {name}" for name in loaded)
            await interaction.response.send_message(f"📂 Loaded Cogs:\n{cog_list}")

    @nextcord.slash_command(
        name="system_info",
        description="Get basic system information."
    )
    async def system_info(self, interaction: nextcord.Interaction):
        """
        Report OS, CPU, and total RAM.
        """
        system = platform.system()
        release = platform.release()
        cpu = platform.processor()
        total_ram = psutil.virtual_memory().total / (1024 ** 3)  # Convert bytes to GB
        await interaction.response.send_message(
            f"🖥 **System Information:**\n"
            f"- OS: {system} {release}\n"
            f"- CPU: {cpu}\n"
            f"- Memory: {total_ram:.2f} GB"
        )

def setup(bot: commands.Bot):
    bot.add_cog(DiagnosticCog(bot))
    logger.info("✅ Loaded DiagnosticCog")
