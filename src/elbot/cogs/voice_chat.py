"""Voice chat cog enabling Discord voice conversations using OpenAI's Realtime API."""

from __future__ import annotations

import logging

import nextcord
from nextcord.ext import commands
from openai import OpenAI

logger = logging.getLogger("elbot.voice")

openai_client = OpenAI()


class VoiceChatCog(commands.Cog):
    """A cog enabling users to converse with the bot via voice.

    Users must be connected to a voice channel.  This placeholder implementation
    demonstrates the high-level workflow for connecting to a voice channel and
    the intended integration points for OpenAI's Realtime API streaming.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._disabled_guilds: set[int] = set()

    def _voice_chat_enabled(self, guild_id: int | None) -> bool:
        """Return whether voice chat is currently enabled for the guild."""

        if guild_id is None:
            # Allow DMs by default (though the command already requires a guild
            # voice channel).  Keeping this branch avoids edge-case errors if
            # the command changes in the future.
            return True

        return guild_id not in self._disabled_guilds

    @nextcord.slash_command(name="voice_chat", description="Talk to the bot via voice")
    async def voice_chat(self, interaction: nextcord.Interaction) -> None:
        """Join the user's voice channel and prepare for realtime voice exchange."""

        if not self._voice_chat_enabled(interaction.guild_id):
            await interaction.response.send_message(
                "🚫 Voice chat is currently disabled on this server.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(with_message=True)

        voice_state = interaction.user.voice
        if not voice_state or not voice_state.channel:
            await interaction.followup.send(
                "⚠️ You need to be connected to a voice channel to use this command.",
                delete_after=10,
            )
            return

        channel = voice_state.channel
        logger.debug("Connecting to voice channel %s", channel)

        voice_client = interaction.guild.voice_client if interaction.guild else None
        vc = voice_client
        joined_here = False
        try:
            if voice_client:
                if voice_client.channel != channel:
                    await voice_client.move_to(channel)
                vc = voice_client
            else:
                vc = await channel.connect()
                joined_here = True
        except nextcord.ClientException:
            await interaction.followup.send(
                "⚠️ I’m already connected to a voice channel; disconnect me first.",
                delete_after=10,
            )
            return

        logger.info("Connected to voice channel %s for realtime chat", channel)

        try:
            # This is a simplified example.  In a real implementation, you
            # would capture audio from the user's microphone, stream it to
            # `openai_client.realtime.sessions.create` via WebSocket, and play
            # back the generated audio chunks via `vc.play(...)`.  See the
            # Realtime API docs for details on session management.
            await interaction.followup.send(
                "🎤 Voice chat is not yet fully implemented. This is a placeholder.",
                delete_after=10,
            )
        finally:
            logger.info("Disconnecting from voice channel %s", channel)
            if joined_here and vc:
                await vc.disconnect()

    @nextcord.slash_command(
        name="voice_chat_toggle",
        description="Enable or disable voice chat commands on this server.",
        default_member_permissions=nextcord.Permissions(manage_guild=True),
    )
    async def voice_chat_toggle(
        self, interaction: nextcord.Interaction, enabled: bool
    ) -> None:
        """Allow administrators to enable or disable voice chat usage."""

        if interaction.guild_id is None:
            await interaction.response.send_message(
                "⚠️ This command can only be used inside a server.",
                ephemeral=True,
            )
            return

        if enabled:
            self._disabled_guilds.discard(interaction.guild_id)
            status_message = "✅ Voice chat has been enabled for this server."
        else:
            self._disabled_guilds.add(interaction.guild_id)
            status_message = "🚫 Voice chat has been disabled for this server."

        logger.info(
            "Voice chat %s by %s in guild %s",
            "enabled" if enabled else "disabled",
            interaction.user,
            interaction.guild_id,
        )

        await interaction.response.send_message(status_message, ephemeral=True)


def setup(bot: commands.Bot) -> None:
    """Load the voice chat cog into the bot."""

    bot.add_cog(VoiceChatCog(bot))
    logger.info("✅ Loaded VoiceChatCog")
