import logging
import platform
import time

import nextcord
import psutil
from nextcord.ext import application_checks, commands

from elbot.utils import safe_reply

logger = logging.getLogger("elbot.admin")


def moderation_target_error(interaction, member):
    """Discord checks the bot's hierarchy; also enforce the requesting member's."""
    guild = interaction.guild
    if guild is None or member.guild.id != guild.id:
        return "Choose a member of this server."
    if member.id in {interaction.user.id, guild.owner_id, interaction.client.user.id}:
        return "You cannot moderate yourself, the server owner, or this bot."
    if interaction.user.id != guild.owner_id and member.top_role >= interaction.user.top_role:
        return "You can only moderate members below your highest role."
    if guild.me is None or member.top_role >= guild.me.top_role:
        return "That member's role is too high for the bot to moderate."
    return None


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

    @nextcord.slash_command(name="kick", description="Kick a member", dm_permission=False,
                           default_member_permissions=nextcord.Permissions(kick_members=True))
    @application_checks.guild_only()
    @application_checks.has_permissions(kick_members=True)
    @application_checks.bot_has_permissions(kick_members=True)
    async def kick(
        self,
        interaction: nextcord.Interaction,
        member: nextcord.Member,
        reason: str = "No reason provided",
    ) -> None:
        await interaction.response.defer(with_message=True, ephemeral=True)
        error = moderation_target_error(interaction, member)
        if error:
            await safe_reply(interaction, error, ephemeral=True)
            return
        await member.kick(reason=reason)
        await safe_reply(interaction, f"👢 Kicked {member.display_name}", ephemeral=True)

    @nextcord.slash_command(name="ban", description="Ban a member", dm_permission=False,
                           default_member_permissions=nextcord.Permissions(ban_members=True))
    @application_checks.guild_only()
    @application_checks.has_permissions(ban_members=True)
    @application_checks.bot_has_permissions(ban_members=True)
    async def ban(
        self,
        interaction: nextcord.Interaction,
        member: nextcord.Member,
        reason: str = "No reason provided",
    ) -> None:
        await interaction.response.defer(with_message=True, ephemeral=True)
        error = moderation_target_error(interaction, member)
        if error:
            await safe_reply(interaction, error, ephemeral=True)
            return
        await member.ban(reason=reason)
        await safe_reply(interaction, f"🔨 Banned {member.display_name}", ephemeral=True)

    @nextcord.slash_command(name="clear_messages", description="Delete recent messages", dm_permission=False,
                           default_member_permissions=nextcord.Permissions(manage_messages=True))
    @application_checks.guild_only()
    @application_checks.has_permissions(manage_messages=True)
    @application_checks.bot_has_permissions(manage_messages=True, read_message_history=True)
    async def clear_messages(self, interaction: nextcord.Interaction,
                             count: int = nextcord.SlashOption(default=5, min_value=1, max_value=100)) -> None:
        await interaction.response.defer(with_message=True, ephemeral=True)
        if type(count) is not int or not 1 <= count <= 100:
            await safe_reply(interaction, "Choose between 1 and 100 messages.", ephemeral=True)
            return
        await interaction.channel.purge(limit=count)
        await safe_reply(interaction, f"🧹 Deleted {count} messages", ephemeral=True)

    @nextcord.slash_command(
        name="clear_bot_messages",
        description="Delete recent messages sent by the bot",
        dm_permission=False,
        default_member_permissions=nextcord.Permissions(manage_messages=True),
    )
    @application_checks.guild_only()
    @application_checks.has_permissions(manage_messages=True)
    @application_checks.bot_has_permissions(manage_messages=True, read_message_history=True)
    async def clear_bot_messages(self, interaction: nextcord.Interaction,
                                 count: int = nextcord.SlashOption(default=50, min_value=1, max_value=100)) -> None:
        await interaction.response.defer(with_message=True, ephemeral=True)
        if type(count) is not int or not 1 <= count <= 100:
            await safe_reply(interaction, "Choose between 1 and 100 messages.", ephemeral=True)
            return

        def is_bot(msg: nextcord.Message) -> bool:
            return msg.author == self.bot.user

        deleted = await interaction.channel.purge(limit=count, check=is_bot)
        await safe_reply(interaction, f"🧹 Deleted {len(deleted)} bot messages", ephemeral=True)


def setup(bot: commands.Bot) -> None:
    bot.add_cog(DiagnosticCog(bot))
    bot.add_cog(ModerationCog(bot))
    logger.info("✅ Loaded Admin cogs (diagnostic + moderation)")
