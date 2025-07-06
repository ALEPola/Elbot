"""Music playback using Lavalink and Wavelink."""

from __future__ import annotations

import os
import logging
import nextcord
from nextcord.ext import commands
import wavelink

from elbot.config import Config

logger = logging.getLogger("elbot.music")


class Music(commands.Cog):
    """A minimal music cog powered by Lavalink via Wavelink."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.node: wavelink.Node | None = None
        self.connect_task = bot.loop.create_task(self.connect_nodes())

    async def connect_nodes(self) -> None:
        """Connect to the Lavalink node configured in :class:`Config`."""

        await self.bot.wait_until_ready()

        uri = f"http://{Config.LAVALINK_HOST}:{Config.LAVALINK_PORT}"
        self.node = wavelink.Node(uri=uri, password=Config.LAVALINK_PASSWORD)
        try:
            await wavelink.Pool.connect(client=self.bot, nodes=[self.node])
        except Exception as exc:  # pragma: no cover - network error handling
            logger.error("Failed to connect to Lavalink at %s: %s", uri, exc)
            self.node = None
        else:
            logger.info("Connected to Lavalink at %s", uri)

    async def cog_unload(self) -> None:
        if self.node:
            await wavelink.Pool.close()

        for guild in self.bot.guilds:
            if guild.voice_client:
                await guild.voice_client.disconnect()

    async def ensure_voice(
        self, interaction: nextcord.Interaction
    ) -> wavelink.Player | None:
        """Ensure a :class:`wavelink.Player` is connected to the user's channel."""

        if not interaction.user.voice:
            await interaction.response.send_message(
                "You must be in a voice channel to use this command.",
                ephemeral=True,
            )
            return None

        channel = interaction.user.voice.channel
        voice = interaction.guild.voice_client

        if voice and not isinstance(voice, wavelink.Player):
            await voice.disconnect()
            voice = None

        if not voice:
            voice = await channel.connect(cls=wavelink.Player)

        return voice

    @nextcord.slash_command(name="play", description="Play a song from YouTube")
    async def play(self, interaction: nextcord.Interaction, query: str) -> None:
        await interaction.response.defer()

        player = await self.ensure_voice(interaction)
        if not player:
            return

        tracks = await wavelink.Pool.fetch_tracks(query)
        if not tracks:
            await interaction.followup.send("No results found.", ephemeral=True)
            return

        track = tracks[0] if not isinstance(tracks, wavelink.Playlist) else tracks.tracks[0]

        await player.queue.put_wait(track)
        if not player.playing:
            await player.play(await player.queue.get())

        await interaction.followup.send(f"Queued **{track.title}**", ephemeral=True)

    @nextcord.slash_command(name="skip", description="Skip the current song")
    async def skip(self, interaction: nextcord.Interaction) -> None:
        player = interaction.guild.voice_client

        if not player or not isinstance(player, wavelink.Player):
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)
            return

        await player.skip()
        await interaction.response.send_message("⏭ Skipped.", ephemeral=True)

    @nextcord.slash_command(name="stop", description="Stop playback and clear the queue")
    async def stop(self, interaction: nextcord.Interaction) -> None:
        player = interaction.guild.voice_client

        if not player or not isinstance(player, wavelink.Player):
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)
            return

        player.queue.clear()
        await player.stop()
        await interaction.response.send_message("🛑 Stopped.", ephemeral=True)

    @commands.Cog.listener()
    async def on_wavelink_track_end(self, payload: wavelink.TrackEndEventPayload) -> None:
        player = payload.player

        if player and player.queue:
            next_track = await player.queue.get()
            await player.play(next_track)


def setup(bot: commands.Bot) -> None:
    bot.add_cog(Music(bot))
    logger.info("✅ Loaded Music cog")

    @bot.slash_command(name="moan", description="Play a moan sound effect")
    async def moan(interaction: nextcord.Interaction) -> None:
        if not interaction.user.voice:
            await interaction.response.send_message(
                "You need to be in a voice channel to use this command!",
                ephemeral=True,
            )
            return

        if isinstance(interaction.guild.voice_client, wavelink.Player):
            await interaction.response.send_message(
                "Stop music playback before using this command.",
                ephemeral=True,
            )
            return

        sound_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "sounds",
            "56753004_girl-moaning_by_a-sfx_preview.mp3",
        )

        if not os.path.exists(sound_path):
            await interaction.response.send_message(
                "❌ Sound effect not found! Contact the bot administrator.",
                ephemeral=True,
            )
            return

        voice_client = interaction.guild.voice_client
        if not voice_client:
            channel = interaction.user.voice.channel
            voice_client = await channel.connect()

        if voice_client.is_playing():
            voice_client.stop()

        voice_client.play(nextcord.FFmpegPCMAudio(sound_path))
        await interaction.response.send_message("😏", ephemeral=True)

    bot.add_application_command(moan)
