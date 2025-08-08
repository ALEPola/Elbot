"""Music playback using Lavalink and Wavelink."""

from __future__ import annotations

import os
import logging
import asyncio
from pathlib import Path
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
        self._cleanup_lock = asyncio.Lock()

    async def connect_nodes(self) -> None:
        """Connect to the Lavalink node configured in :class:`Config`."""

        await self.bot.wait_until_ready()

        host = os.getenv("LAVALINK_HOST", Config.LAVALINK_HOST)
        port = int(os.getenv("LAVALINK_PORT", str(Config.LAVALINK_PORT)))
        password = os.getenv("LAVALINK_PASSWORD", Config.LAVALINK_PASSWORD)
        uri = f"http://{host}:{port}"

        reconnecting = False
        if wavelink.Pool.is_connected():
            reconnecting = True
            for existing in list(wavelink.Pool.nodes.values()):
                try:
                    await existing.close()
                    logger.info("Closed Lavalink node %s", existing.identifier)
                except Exception as exc:  # pragma: no cover - best effort cleanup
                    logger.warning(
                        "Failed to close Lavalink node %s: %s", existing.identifier, exc
                    )

        try:
            node = wavelink.Node(identifier="MAIN", uri=uri, password=password, client=self.bot)
            nodes = await wavelink.Pool.connect(nodes=[node], client=self.bot)
            self.node = nodes.get("MAIN")
        except Exception as exc:  # pragma: no cover - network error handling
            if reconnecting:
                logger.error("Failed to reconnect to Lavalink at %s: %s", uri, exc)
            else:
                logger.error("Failed to connect to Lavalink at %s: %s", uri, exc)
            self.node = None
        else:
            if reconnecting:
                logger.info("Reconnected to Lavalink at %s", uri)
            else:
                logger.info("Connected to Lavalink at %s", uri)

    async def close_node(self) -> None:
        """Close the Lavalink connection if active."""
        async with self._cleanup_lock:
            if self.node:
                try:
                    await self.node.close()
                finally:
                    self.node = None
                    logger.info("Lavalink connection closed")

    async def cog_unload(self) -> None:
        await self.close_node()

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

        if self.connect_task and not self.connect_task.done():
            await self.connect_task

        if not self.node or self.node.status != wavelink.NodeStatus.CONNECTED:
            await interaction.response.send_message(
                "The music node is not ready. Please try again in a moment.",
                ephemeral=True,
            )
            return None

        if voice and not isinstance(voice, wavelink.Player):
            await voice.disconnect()
            voice = None
        elif voice and voice.channel.id != channel.id:
            await voice.move_to(channel)
            message = f"Moved to {channel.mention}."
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)

        if not voice:
            voice = await channel.connect(cls=wavelink.Player)

        return voice

    @nextcord.slash_command(name="play", description="Play a song from YouTube")
    async def play(self, interaction: nextcord.Interaction, query: str) -> None:
        player = await self.ensure_voice(interaction)
        if not player:
            return

        await interaction.response.defer()

        if self.connect_task and not self.connect_task.done():
            await self.connect_task

        node = self.node if self.node else None
        if not node or node.status != wavelink.NodeStatus.CONNECTED:
            await interaction.followup.send(
                "The music node is not ready. Please try again in a moment.",
                ephemeral=True,
            )
            return

        node = wavelink.Pool.get_node()
        try:
            search = await wavelink.Playable.search(query, node=node)
        except Exception as exc:  # pragma: no cover - search failure
            logger.error("Track search failed: %s", exc)
            await interaction.followup.send("Search failed.", ephemeral=True)
            return

        if isinstance(search, wavelink.Playlist):
            track = search[0]
        else:
            if not search:
                await interaction.followup.send("No results found.", ephemeral=True)
                return
            track = search[0]

        await player.queue.put_wait(track)
        if not player.playing:
            await player.play(await player.queue.get())

        await interaction.followup.send(f"Queued **{track.title}**", ephemeral=True)

    @nextcord.slash_command(name="queue", description="Show the upcoming tracks")
    async def queue(self, interaction: nextcord.Interaction) -> None:
        """Display the current music queue."""
        player = interaction.guild.voice_client

        if not player or not isinstance(player, wavelink.Player):
            await interaction.response.send_message(
                "Nothing is playing.", ephemeral=True
            )
            return

        queue = list(player.queue)
        if not queue:
            await interaction.response.send_message(
                "The queue is empty.", ephemeral=True
            )
            return

        lines = [f"{index + 1}. {track.title}" for index, track in enumerate(queue)]
        message = "\n".join(lines)

        await interaction.response.send_message(message, ephemeral=True)

    @nextcord.slash_command(name="skip", description="Skip the current song")
    async def skip(self, interaction: nextcord.Interaction) -> None:
        player = interaction.guild.voice_client

        if not player or not isinstance(player, wavelink.Player):
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)
            return

        await player.skip()
        await interaction.response.send_message("⏭ Skipped.", ephemeral=True)

    @nextcord.slash_command(name="stop", description="Stop playback and disconnect")
    async def stop(self, interaction: nextcord.Interaction) -> None:
        player = interaction.guild.voice_client

        if not player or not isinstance(player, wavelink.Player):
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)
            return

        player.queue.clear()
        await player.stop()
        await player.disconnect()
        await interaction.response.send_message("🛑 Stopped playback.", ephemeral=True)

    @commands.Cog.listener()
    async def on_wavelink_track_end(self, payload: wavelink.TrackEndEventPayload) -> None:
        player = payload.player

        if player and player.queue:
            next_track = await player.queue.get()
            await player.play(next_track)
        elif player and not player.queue:
            await player.disconnect()


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

        base = Path(__file__).resolve().parent.parent / "sfx"
        sound_path = base / "moan.mp3"

        if not sound_path.exists():
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

        voice_client.play(nextcord.FFmpegPCMAudio(str(sound_path)))
        await interaction.response.send_message("😏", ephemeral=True)

    bot.add_application_command(moan)
