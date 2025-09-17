"""Music playback using Mafic (Lavalink v4) with yt-dlp fallback."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from collections import deque
from dataclasses import dataclass, field
from typing import Deque

import nextcord
from nextcord.ext import commands

from elbot.audio.lavalink_client import (
    DisabledLavalinkManager,
    LavalinkManager,
    LavalinkTrack,
    MusicError,
    NodeNotReadyError,
    NoResultsError,
)
from elbot.config import Config
from elbot.audio.mafic_compat import get_mafic

mafic = get_mafic()

logger = logging.getLogger("elbot.music")

FFMPEG_PATH = os.getenv("FFMPEG_PATH") or shutil.which("ffmpeg") or "ffmpeg"


@dataclass(slots=True)
class QueueEntry:
    """A queued track request."""

    track: LavalinkTrack
    channel_id: int
    query: str


@dataclass(slots=True)
class MusicState:
    """Per-guild music state management."""

    guild_id: int
    player: mafic.Player | None = None
    queue: Deque[QueueEntry] = field(default_factory=deque)
    now_playing: QueueEntry | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def reset(self) -> None:
        self.queue.clear()
        self.now_playing = None


def truncate(text: str, limit: int = 80) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


class Music(commands.Cog):
    """Discord music commands backed by Lavalink."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._manager_error: str | None = None
        try:
            self.manager = LavalinkManager(bot)
        except RuntimeError as exc:
            reason = str(exc)
            self._manager_error = reason
            self.manager = DisabledLavalinkManager(bot, reason)
        self._states: dict[int, MusicState] = {}

    # ------------------------------------------------------------------
    # Utility helpers
    # ------------------------------------------------------------------
    def _get_state(self, guild_id: int) -> MusicState:
        state = self._states.get(guild_id)
        if state is None:
            state = MusicState(guild_id=guild_id)
            self._states[guild_id] = state
        return state

    async def _cleanup_state(self, guild_id: int) -> None:
        state = self._states.get(guild_id)
        if not state:
            return
        if state.player:
            try:
                await state.player.disconnect(force=True)
            except Exception:  # pragma: no cover - defensive cleanup
                pass
            state.player = None
        self._states.pop(guild_id, None)

    async def cog_unload(self) -> None:  # type: ignore[override]
        if self.manager:
            await self.manager.close()
        for guild in list(self.bot.guilds):
            state = self._states.get(guild.id)
            if state and state.player:
                try:
                    await state.player.disconnect(force=True)
                except Exception:
                    pass
        self._states.clear()

    async def ensure_voice(
        self, interaction: nextcord.Interaction
    ) -> tuple[mafic.Player | None, str | None]:
        """Ensure we are connected to the requester's voice channel."""

        if not interaction.user or not interaction.user.voice:
            return None, "You must join a voice channel first."

        guild = interaction.guild
        assert guild is not None  # for type checkers

        state = self._get_state(guild.id)
        voice = guild.voice_client

        if self._manager_error:
            return None, self._manager_error

        if not await self.manager.wait_ready(timeout=5):
            return None, "The music node is not ready. Try again shortly."

        if voice and not isinstance(voice, mafic.Player):
            try:
                await voice.disconnect(force=True)
            except Exception:
                pass
            voice = None
            state.player = None

        if voice and voice.channel.id != interaction.user.voice.channel.id:
            try:
                await voice.disconnect(force=True)
            except Exception:
                pass
            voice = None
            state.player = None

        if voice is None:
            try:
                voice = await interaction.user.voice.channel.connect(cls=mafic.Player)
            except Exception as exc:
                logger.error("Voice connection failed: %s", exc)
                return None, "Could not join your voice channel."

        state.player = voice
        return voice, None

    async def _start_next(self, guild_id: int) -> None:
        state = self._states.get(guild_id)
        if not state or not state.player:
            return

        async with state.lock:
            if state.now_playing is not None:
                return
            if not state.queue:
                # Nothing queued – disconnect after stopping playback.
                try:
                    await state.player.disconnect(force=True)
                except Exception:
                    pass
                await self._cleanup_state(guild_id)
                return

            entry = state.queue.popleft()
            state.now_playing = entry

        try:
            await state.player.play(entry.track.track)
        except Exception as exc:
            logger.error("Failed to start playback: %s", exc)
            state.now_playing = None
            await self._notify_channel(
                guild_id, entry.channel_id, "Playback failed. Trying the next track."
            )
            await self._start_next(guild_id)
            return

        await self._notify_channel(
            guild_id,
            entry.channel_id,
            f"▶️ Now playing **{truncate(entry.track.title)}**",
        )

    async def _notify_channel(self, guild_id: int, channel_id: int, message: str) -> None:
        channel = self.bot.get_channel(channel_id)
        if not channel or not isinstance(channel, nextcord.TextChannel):
            return
        try:
            await channel.send(message)
        except Exception:  # pragma: no cover - ignore permission failures
            pass

    # ------------------------------------------------------------------
    # Slash commands
    # ------------------------------------------------------------------
    @nextcord.slash_command(name="play", description="Play a song from YouTube")
    async def play(self, interaction: nextcord.Interaction, query: str) -> None:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=False)

        player, msg = await self.ensure_voice(interaction)
        if msg:
            await interaction.followup.send(msg, ephemeral=True)
            return
        if player is None:
            return

        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("This command can only be used in a server.", ephemeral=True)
            return

        state = self._get_state(guild.id)
        state.player = player

        try:
            lavalink_track = await self.manager.resolve(
                query, requester_id=interaction.user.id
            )
        except NodeNotReadyError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except NoResultsError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except MusicError as exc:  # pragma: no cover - safety net
            await interaction.followup.send(f"Playback failed: {exc}", ephemeral=True)
            return

        entry = QueueEntry(track=lavalink_track, channel_id=interaction.channel_id, query=query)
        state.queue.append(entry)

        suffix = " (yt-dlp fallback)" if lavalink_track.source == "yt-dlp" else ""

        if state.now_playing is None and getattr(player, "current", None) is None:
            state.now_playing = None  # ensure start picks new track
            await self._start_next(guild.id)
            response = f"▶️ Now playing **{truncate(lavalink_track.title)}**{suffix}"
        else:
            response = f"⏭ Queued **{truncate(lavalink_track.title)}**{suffix}"

        await interaction.followup.send(response)

    @nextcord.slash_command(name="queue", description="Show the upcoming tracks")
    async def queue(self, interaction: nextcord.Interaction) -> None:
        state = self._states.get(interaction.guild_id or 0)
        if not state or (not state.queue and not state.now_playing):
            await interaction.response.send_message("The queue is empty.", ephemeral=True)
            return

        embed = nextcord.Embed(title="Music queue", color=nextcord.Color.blurple())
        if state.now_playing:
            embed.add_field(
                name="Now playing",
                value=truncate(state.now_playing.track.title),
                inline=False,
            )
        if state.queue:
            lines = [
                f"{idx + 1}. {truncate(entry.track.title)}" for idx, entry in enumerate(state.queue)
            ]
            embed.add_field(name="Up next", value="\n".join(lines), inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @nextcord.slash_command(name="skip", description="Skip the current song")
    async def skip(self, interaction: nextcord.Interaction) -> None:
        state = self._states.get(interaction.guild_id or 0)
        if not state or not state.player:
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)
            return

        try:
            await state.player.stop()
        except Exception as exc:
            logger.error("Skip failed: %s", exc)
            await interaction.response.send_message("Unable to skip right now.", ephemeral=True)
            return

        state.now_playing = None
        await interaction.response.send_message("⏭ Skipped.", ephemeral=True)

    @nextcord.slash_command(name="stop", description="Stop playback and disconnect")
    async def stop(self, interaction: nextcord.Interaction) -> None:
        state = self._states.get(interaction.guild_id or 0)
        if not state or not state.player:
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)
            return

        state.reset()
        try:
            await state.player.stop()
        except Exception:
            pass
        try:
            await state.player.disconnect(force=True)  # type: ignore[arg-type]
        except Exception:
            pass
        await interaction.response.send_message("🛑 Stopped playback.", ephemeral=True)
        await self._cleanup_state(interaction.guild_id or 0)

    # ------------------------------------------------------------------
    # Mafic event listeners
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_node_ready(self, node: mafic.Node) -> None:  # type: ignore[override]
        self.manager.handle_node_ready(node)

    @commands.Cog.listener()
    async def on_node_unavailable(self, node: mafic.Node) -> None:  # type: ignore[override]
        self.manager.handle_node_unavailable(node)

    @commands.Cog.listener()
    async def on_track_end(self, event: mafic.TrackEndEvent) -> None:  # type: ignore[override]
        guild_id = getattr(event.player.guild, "id", None)
        if guild_id is None:
            return
        state = self._states.get(guild_id)
        if not state:
            return
        if state.now_playing and event.track:
            current_id = state.now_playing.track.track.id
            if current_id != event.track.id:
                return
        state.now_playing = None
        await self._start_next(guild_id)

    @commands.Cog.listener()
    async def on_track_exception(self, event: mafic.TrackExceptionEvent) -> None:  # type: ignore[override]
        guild_id = getattr(event.player.guild, "id", None)
        if guild_id is None:
            return
        state = self._states.get(guild_id)
        if not state or not state.now_playing:
            return
        await self._notify_channel(
            guild_id,
            state.now_playing.channel_id,
            "⚠️ Encountered a playback error. Retrying with the next track...",
        )
        state.now_playing = None
        await self._start_next(guild_id)


def setup(bot: commands.Bot) -> None:
    bot.add_cog(Music(bot))
    logger.info("✅ Loaded Music cog")

    @bot.slash_command(name="moan", description="Play a moan sound effect")
    async def moan(interaction: nextcord.Interaction) -> None:
        if not interaction.user or not interaction.user.voice:
            await interaction.response.send_message(
                "You need to be in a voice channel to use this command!",
                ephemeral=True,
            )
            return

        if isinstance(interaction.guild.voice_client, mafic.Player):
            await interaction.response.send_message(
                "Stop music playback before using this command.",
                ephemeral=True,
            )
            return

        base_dir = Config.BASE_DIR
        sound_path = base_dir / "sfx" / "moan.mp3"
        if not sound_path.exists():
            await interaction.response.send_message("❌ Sound effect not found!", ephemeral=True)
            return

        vc = interaction.guild.voice_client
        if not vc:
            vc = await interaction.user.voice.channel.connect()

        if vc.is_playing():
            vc.stop()

        source = nextcord.FFmpegPCMAudio(str(sound_path), executable=FFMPEG_PATH)
        vc.play(source)
        await interaction.response.send_message("😏", ephemeral=True)

