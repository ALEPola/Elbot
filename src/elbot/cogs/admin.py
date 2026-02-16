import logging
import platform
import time

import nextcord
import psutil
from nextcord.ext import commands

from elbot.utils import safe_reply

logger = logging.getLogger("elbot.admin")


class DiagnosticCog(commands.Cog):
    """Basic bot and system diagnostics."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.start_time = time.time()

    @nextcord.slash_command(name="uptime", description="Check the bot's uptime.")
    async def uptime(self, interaction: nextcord.Interaction) -> None:
        await interaction.response.defer(with_message=True)
        uptime_seconds = time.time() - self.start_time
        hours, remainder = divmod(int(uptime_seconds), 3600)
        minutes, seconds = divmod(remainder, 60)
        await safe_reply(interaction, f"🕒 Uptime: {hours}h {minutes}m {seconds}s")

    @nextcord.slash_command(name="ping", description="Check the bot's latency.")
    async def ping(self, interaction: nextcord.Interaction) -> None:
        await interaction.response.defer(with_message=True)
        latency_ms = round(self.bot.latency * 1000)
        await safe_reply(interaction, f"🏓 Latency: {latency_ms}ms")

    @nextcord.slash_command(name="cogs", description="List all loaded cogs.")
    async def cogs(self, interaction: nextcord.Interaction) -> None:
        await interaction.response.defer(with_message=True)
        loaded = list(self.bot.cogs.keys())
        if not loaded:
            await safe_reply(interaction, "No cogs are currently loaded.")
            return
        await safe_reply(interaction, "📂 Loaded Cogs:\n" + "\n".join(f"- {name}" for name in loaded))

    @nextcord.slash_command(name="system_info", description="Get basic system information.")
    async def system_info(self, interaction: nextcord.Interaction) -> None:
        await interaction.response.defer(with_message=True)
        system = platform.system()
        release = platform.release()
        cpu = platform.processor()
        total_ram = psutil.virtual_memory().total / (1024 ** 3)
        await safe_reply(
            interaction,
            f"🖥 **System Information:**\n"
            f"- OS: {system} {release}\n"
            f"- CPU: {cpu}\n"
            f"- Memory: {total_ram:.2f} GB",
        )


class ModerationCog(commands.Cog):
    """Basic moderation commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @nextcord.slash_command(name="kick", description="Kick a member")
    @commands.has_permissions(kick_members=True)
    async def kick(
        self,
        interaction: nextcord.Interaction,
        member: nextcord.Member,
        reason: str = "No reason provided",
    ) -> None:
        await interaction.response.defer(with_message=True, ephemeral=True)
        await member.kick(reason=reason)
        await safe_reply(interaction, f"👢 Kicked {member.display_name}", ephemeral=True)

    @nextcord.slash_command(name="ban", description="Ban a member")
    @commands.has_permissions(ban_members=True)
    async def ban(
        self,
        interaction: nextcord.Interaction,
        member: nextcord.Member,
        reason: str = "No reason provided",
    ) -> None:
        await interaction.response.defer(with_message=True, ephemeral=True)
        await member.ban(reason=reason)
        await safe_reply(interaction, f"🔨 Banned {member.display_name}", ephemeral=True)

    @nextcord.slash_command(name="clear_messages", description="Delete recent messages")
    @commands.has_permissions(manage_messages=True)
    async def clear_messages(self, interaction: nextcord.Interaction, count: int = 5) -> None:
        await interaction.response.defer(with_message=True, ephemeral=True)
        await interaction.channel.purge(limit=count)
        await safe_reply(interaction, f"🧹 Deleted {count} messages", ephemeral=True)

    @nextcord.slash_command(
        name="clear_bot_messages",
        description="Delete recent messages sent by the bot",
    )
    @commands.has_permissions(manage_messages=True)
    async def clear_bot_messages(self, interaction: nextcord.Interaction, count: int = 50) -> None:
        await interaction.response.defer(with_message=True, ephemeral=True)

        def is_bot(msg: nextcord.Message) -> bool:
            return msg.author == self.bot.user

        deleted = await interaction.channel.purge(limit=count, check=is_bot)
        await safe_reply(interaction, f"🧹 Deleted {len(deleted)} bot messages", ephemeral=True)


def setup(bot: commands.Bot) -> None:
    bot.add_cog(DiagnosticCog(bot))
    bot.add_cog(ModerationCog(bot))
    logger.info("✅ Loaded Admin cogs (diagnostic + moderation)")
