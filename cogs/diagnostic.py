import platform
import psutil
import nextcord
from nextcord.ext import commands
from datetime import datetime, timedelta

class DiagnosticCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.start_time = datetime.now()

    @nextcord.slash_command(name="uptime", description="Check the bot's uptime.")
    async def uptime(self, interaction: nextcord.Interaction):
        uptime_duration = datetime.now() - self.start_time
        uptime_str = str(timedelta(seconds=int(uptime_duration.total_seconds())))
        await interaction.response.send_message(f"🕒 Uptime: {uptime_str}")

    @nextcord.slash_command(name="ping", description="Check the bot's latency.")
    async def ping(self, interaction: nextcord.Interaction):
        latency = round(self.bot.latency * 1000)  # Convert to ms
        await interaction.response.send_message(f"🏓 Latency: {latency}ms")

    @nextcord.slash_command(name="cogs", description="List all loaded cogs.")
    async def cogs(self, interaction: nextcord.Interaction):
        loaded_cogs = list(self.bot.cogs.keys())
        cogs_list = "\n".join(loaded_cogs) if loaded_cogs else "No cogs loaded."
        await interaction.response.send_message(f"📂 Loaded Cogs:\n{cogs_list}")

    @nextcord.slash_command(name="system_info", description="Get system information.")
    async def system_info(self, interaction: nextcord.Interaction):
        system = platform.system()
        release = platform.release()
        version = platform.version()
        cpu = platform.processor()
        memory = psutil.virtual_memory().total / (1024 ** 3)  # Convert to GB
        await interaction.response.send_message(
            f"🖥 **System Information:**\n"
            f"- OS: {system} {release} ({version})\n"
            f"- CPU: {cpu}\n"
            f"- Memory: {memory:.2f} GB"
        )

def setup(bot):
    bot.add_cog(DiagnosticCog(bot))
    print("✅ Loaded DiagnosticCog")