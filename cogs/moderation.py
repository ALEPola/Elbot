import nextcord
from nextcord.ext import commands

import logging

logger = logging.getLogger("elbot.moderation")

class ModerationCog(commands.Cog):
    """Basic moderation commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @nextcord.slash_command(name="kick", description="Kick a member")
    @commands.has_permissions(kick_members=True)
    async def kick(self, interaction: nextcord.Interaction, member: nextcord.Member, reason: str = "No reason provided"):
        await member.kick(reason=reason)
        await interaction.response.send_message(f"👢 Kicked {member.display_name}", ephemeral=True)

    @nextcord.slash_command(name="ban", description="Ban a member")
    @commands.has_permissions(ban_members=True)
    async def ban(self, interaction: nextcord.Interaction, member: nextcord.Member, reason: str = "No reason provided"):
        await member.ban(reason=reason)
        await interaction.response.send_message(f"🔨 Banned {member.display_name}", ephemeral=True)

    @nextcord.slash_command(name="clear_messages", description="Delete recent messages")
    @commands.has_permissions(manage_messages=True)
    async def clear_messages(self, interaction: nextcord.Interaction, count: int = 5):
        await interaction.channel.purge(limit=count)
        await interaction.response.send_message(f"🧹 Deleted {count} messages", ephemeral=True)

    @nextcord.slash_command(
        name="clear_bot_messages",
        description="Delete recent messages sent by the bot",
    )
    @commands.has_permissions(manage_messages=True)
    async def clear_bot_messages(
        self, interaction: nextcord.Interaction, count: int = 50
    ):
        """Remove up to ``count`` recent messages authored by this bot."""

        def is_bot(msg: nextcord.Message) -> bool:
            return msg.author == self.bot.user

        deleted = await interaction.channel.purge(limit=count, check=is_bot)
        await interaction.response.send_message(
            f"🧹 Deleted {len(deleted)} bot messages", ephemeral=True
        )


def setup(bot: commands.Bot):
    bot.add_cog(ModerationCog(bot))
    logger.info("✅ Loaded ModerationCog")
