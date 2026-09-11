"""Explicit, short local speaker tests. No transcripts or cloud audio."""

import nextcord
from nextcord.ext import commands


class Listen(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @nextcord.slash_command(name="listen", description="Test local speaker reception (no recording)")
    async def listen(self, interaction: nextcord.Interaction):
        pass

    def _player(self, interaction):
        player = interaction.guild.voice_client if interaction.guild else None
        return player if hasattr(player, "start_listening") else None

    @listen.subcommand(name="start", description="Listen locally for two minutes; no saved audio")
    async def start(self, interaction: nextcord.Interaction):
        if not interaction.guild or not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("Manage Server permission is required.", ephemeral=True)
            return
        player = self._player(interaction)
        if player is None:
            await interaction.response.send_message(
                "The shared voice bridge must be enabled and connected first. Start music, then retry.", ephemeral=True,
            )
            return
        if getattr(getattr(interaction.user, "voice", None), "channel", None) != player.channel:
            await interaction.response.send_message("Join ELBOT's voice channel first.", ephemeral=True)
            return
        # Visible notification precedes subscription to incoming audio.
        await interaction.response.send_message(
            "ELBOT is starting a two-minute local speaker test in " + player.channel.mention
            + ". Audio stays in memory only until each phrase ends; no recordings, transcripts, or cloud processing."
            + " Say \"ELBOT\" to test the wake phrase. Use /listen stop to end it."
        )
        channel = interaction.channel

        async def notify(event, outcome):
            if channel is None or outcome in ("extended", "held"):
                return
            if outcome == "busy":
                text = f"One at a time — **{event.speaker.display_name}**, hang on."
            else:
                text = f"Heard my name from **{event.speaker.display_name}**."
            await channel.send(text)

        try:
            await player.start_listening(notify=notify)
        except Exception:
            await interaction.followup.send("The listener could not start. No audio test is active.")
            return
        if player.wake is None:
            await interaction.followup.send(
                "Speaker detection is on, but wake-phrase detection is unavailable on this host.", ephemeral=True,
            )

    @listen.subcommand(name="stop", description="Stop local listening and clear audio buffers")
    async def stop(self, interaction: nextcord.Interaction):
        player = self._player(interaction)
        if player is None:
            await interaction.response.send_message("No shared voice listener is connected.", ephemeral=True)
            return
        member_channel = getattr(getattr(interaction.user, "voice", None), "channel", None)
        if member_channel != player.channel and not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("Join ELBOT's voice channel to stop the test.", ephemeral=True)
            return
        await player.stop_listening()
        await interaction.response.send_message("Local listening stopped; audio buffers cleared.")

    @listen.subcommand(name="status", description="Show local speaker-test state")
    async def status(self, interaction: nextcord.Interaction):
        player = self._player(interaction)
        if player is None:
            await interaction.response.send_message("Shared voice listener is not connected.", ephemeral=True)
            return
        wake = player.wake
        wake_text = (
            f"Wake phrases: {player.wake_count} · Utterances checked: {wake.metrics['utterances']}"
            if wake is not None else "Wake detection: off"
        )
        await interaction.response.send_message(
            f"Listening: {'yes' if player.audio.active else 'no'} · "
            f"PCM frames: {player.audio.metrics['frames']} · Decode errors: {player.receive_errors} · "
            + wake_text,
            ephemeral=True,
        )


def setup(bot):
    bot.add_cog(Listen(bot))
