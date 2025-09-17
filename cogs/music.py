"""Nextcord music cog backed by Lavalink v4 with yt-dlp fallback."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Dict, Optional

import mafic
import nextcord
from nextcord.ext import commands

from elbot.music import EmbedFactory, FallbackPlayer, LavalinkAudioBackend, MusicQueue, QueuedTrack
from elbot.music.audio_backend import TrackLoadFailure
from elbot.music.cookies import CookieManager
from elbot.music.diagnostics import DiagnosticsService
from elbot.music.logging_config import configure_json_logging
from elbot.music.metrics import PlaybackMetrics

_LOGGING_INITIALISED = False


def _ensure_logging() -> None:
    global _LOGGING_INITIALISED
    if not _LOGGING_INITIALISED:
        configure_json_logging()
        _LOGGING_INITIALISED = True


def _lavalink_config() -> tuple[str, int, str, bool]:
    host = os.getenv("LAVALINK_HOST", "127.0.0.1")
    port = int(os.getenv("LAVALINK_PORT", "2333"))
    password = os.getenv("LAVALINK_PASSWORD", "youshallnotpass")
    secure = os.getenv("LAVALINK_SSL", "false").lower() == "true"
    return host, port, password, secure


@dataclass
class GuildState:
    queue: MusicQueue = field(default_factory=MusicQueue)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    now_playing: Optional[QueuedTrack] = None
    player: Optional[mafic.Player] = None
    last_channel_id: Optional[int] = None


class Music(commands.Cog):
    """Slash command music cog with resilient fallback playback."""

    def __init__(self, bot: commands.Bot) -> None:
        _ensure_logging()
        self.bot = bot
        self.logger = logging.getLogger("elbot.music")
        self.backend = LavalinkAudioBackend(bot)
        self.metrics = PlaybackMetrics()
        self.cookies = CookieManager()
        self.fallback = FallbackPlayer(self.backend, cookies=self.cookies, metrics=self.metrics)
        self.embed_factory = EmbedFactory()
        host, port, password, secure = _lavalink_config()
        self.diagnostics = DiagnosticsService(
            host=host,
            port=port,
            password=password,
            secure=secure,
            cookies=self.cookies,
            metrics=self.metrics,
        )
        self._states: Dict[int, GuildState] = {}

    # ------------------------------------------------------------------
    # Cog lifecycle
    # ------------------------------------------------------------------
    async def cog_load(self) -> None:  # type: ignore[override]
        await self.backend.wait_ready()

    async def cog_unload(self) -> None:  # type: ignore[override]
        for guild_id, state in list(self._states.items()):
            await self._disconnect(guild_id, state)
        await self.backend.close()

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------
    def _get_state(self, guild_id: int) -> GuildState:
        if guild_id not in self._states:
            self._states[guild_id] = GuildState()
        return self._states[guild_id]

    async def _disconnect(self, guild_id: int, state: GuildState) -> None:
        if state.player:
            try:
                await state.player.disconnect(force=True)
            except Exception:  # pragma: no cover - defensive cleanup
                pass
        self._states.pop(guild_id, None)

    async def _ensure_voice(self, interaction: nextcord.Interaction) -> tuple[Optional[mafic.Player], Optional[str]]:
        user = interaction.user
        if user is None or not isinstance(user, nextcord.Member) or user.voice is None:
            return None, "You must join a voice channel first."
        guild = interaction.guild
        if guild is None:
            return None, "This command can only be used in guilds."

        if not await self.backend.wait_ready():
            return None, "Lavalink node is not ready."

        state = self._get_state(guild.id)
        voice = guild.voice_client
        if voice and not isinstance(voice, mafic.Player):
            try:
                await voice.disconnect(force=True)
            except Exception:
                pass
            voice = None
            state.player = None

        target_channel = user.voice.channel
        if voice and voice.channel != target_channel:
            try:
                await voice.move_to(target_channel)
            except Exception:
                await voice.disconnect(force=True)
                voice = None
                state.player = None

        if voice is None:
            try:
                voice = await target_channel.connect(cls=mafic.Player)
            except Exception as exc:
                self.logger.error("Voice connection failed", exc_info=exc)
                return None, "Could not join your voice channel."

        state.player = voice
        state.last_channel_id = interaction.channel_id
        return voice, None

    def _calculate_eta_ms(self, guild_id: int) -> int:
        state = self._get_state(guild_id)
        eta = 0
        if state.now_playing and state.player:
            position = getattr(state.player, "position", 0)
            eta += max(state.now_playing.handle.duration - int(position), 0)
        for entry in state.queue.snapshot():
            eta += entry.handle.duration
        return eta

    async def _begin_playback(self, guild_id: int) -> None:
        state = self._get_state(guild_id)
        player = state.player
        if player is None:
            return
        if state.now_playing is not None:
            return
        next_track = state.queue.pop_next()
        if not next_track:
            return
        state.now_playing = next_track
        try:
            await player.play(next_track.handle.track)
            self.metrics.incr_started()
            await self._announce_now_playing(guild_id)
        except Exception as exc:  # pragma: no cover - network errors
            self.metrics.incr_failed()
            self.logger.error("Failed to start playback", exc_info=exc)
            state.now_playing = None
            await self._begin_playback(guild_id)

    async def _announce_now_playing(self, guild_id: int) -> None:
        state = self._get_state(guild_id)
        track = state.now_playing
        if not track:
            return
        channel_id = track.channel_id or state.last_channel_id
        if not channel_id:
            return
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except Exception:  # pragma: no cover - network failure
                return
        if isinstance(channel, nextcord.abc.Messageable):
            embed = self.embed_factory.now_playing(track, position=0, eta_ms=0)
            await channel.send(embed=embed)

    async def _ensure_playing(self, guild_id: int) -> None:
        state = self._get_state(guild_id)
        if state.now_playing is None:
            await self._begin_playback(guild_id)

    async def _stop(self, guild_id: int) -> None:
        state = self._get_state(guild_id)
        state.queue.clear()
        state.now_playing = None
        if state.player:
            try:
                await state.player.stop()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Slash commands
    # ------------------------------------------------------------------
    @nextcord.slash_command(name="play", description="Play a YouTube track")
    async def play(self, interaction: nextcord.Interaction, query: str) -> None:
        await interaction.response.defer()
        player, error = await self._ensure_voice(interaction)
        if error:
            await interaction.followup.send(embed=self.embed_factory.failure(error), ephemeral=True)
            return
        assert interaction.guild is not None
        state = self._get_state(interaction.guild.id)
        async with state.lock:
            eta_ms = self._calculate_eta_ms(interaction.guild.id)
            try:
                queued_track = await self.fallback.build_queue_entry(
                    query,
                    requested_by=interaction.user.id if interaction.user else 0,
                    requester_display=str(interaction.user),
                    channel_id=interaction.channel_id,
                )
            except TrackLoadFailure as exc:
                self.metrics.incr_failed()
                await interaction.followup.send(
                    embed=self.embed_factory.failure(str(exc)), ephemeral=True
                )
                return
            state.queue.add(queued_track)
            queue_position = len(state.queue)
            await interaction.followup.send(
                embed=self.embed_factory.queued(
                    queued_track,
                    position=queue_position,
                    eta_ms=eta_ms,
                )
            )
            await self._ensure_playing(interaction.guild.id)

    @nextcord.slash_command(name="skip", description="Skip the current track")
    async def skip(self, interaction: nextcord.Interaction) -> None:
        await interaction.response.defer()
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("This command can only be used in guilds.", ephemeral=True)
            return
        state = self._get_state(guild.id)
        if not state.player or not state.now_playing:
            await interaction.followup.send("Nothing is playing right now.", ephemeral=True)
            return
        await state.player.stop()
        state.now_playing = None
        await interaction.followup.send("Skipped the current track.")
        await self._ensure_playing(guild.id)

    @nextcord.slash_command(name="stop", description="Stop playback and clear the queue")
    async def stop(self, interaction: nextcord.Interaction) -> None:
        await interaction.response.defer()
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("This command can only be used in guilds.", ephemeral=True)
            return
        await self._stop(guild.id)
        await interaction.followup.send("Playback stopped and queue cleared.")

    @nextcord.slash_command(name="queue", description="Show the current queue")
    async def show_queue(self, interaction: nextcord.Interaction) -> None:
        await interaction.response.defer()
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("This command can only be used in guilds.", ephemeral=True)
            return
        state = self._get_state(guild.id)
        tracks = state.queue.snapshot()
        if not tracks:
            embed = self.embed_factory.queue_page(
                [],
                page=0,
                per_page=8,
                total=0,
                now_playing=state.now_playing,
            )
            await interaction.followup.send(embed=embed)
            return
        from elbot.music.embeds import QueuePaginator  # lazy import to avoid circular

        paginator = QueuePaginator(
            self.embed_factory,
            tracks,
            per_page=8,
            now_playing=state.now_playing,
        )
        await paginator.send_initial(interaction)

    @nextcord.slash_command(name="remove", description="Remove a queued track")
    async def remove(self, interaction: nextcord.Interaction, target: str) -> None:
        await interaction.response.defer()
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("This command can only be used in guilds.", ephemeral=True)
            return
        state = self._get_state(guild.id)
        removed: Optional[QueuedTrack] = None
        removed_many = []
        if "-" in target:
            try:
                start_str, end_str = target.split("-", 1)
                start = int(start_str.strip()) - 1
                end = int(end_str.strip()) - 1
            except ValueError:
                await interaction.followup.send("Invalid range.", ephemeral=True)
                return
            removed_many = state.queue.remove_range(start, end)
        else:
            try:
                index = int(target) - 1
            except ValueError:
                await interaction.followup.send("Invalid index.", ephemeral=True)
                return
            removed = state.queue.remove_index(index)
        if removed_many:
            await interaction.followup.send(f"Removed {len(removed_many)} tracks from the queue.")
        elif removed:
            await interaction.followup.send(f"Removed **{removed.handle.title}** from the queue.")
        else:
            await interaction.followup.send("No tracks removed.", ephemeral=True)

    @nextcord.slash_command(name="move", description="Move a track to a different position")
    async def move(self, interaction: nextcord.Interaction, source: int, destination: int) -> None:
        await interaction.response.defer()
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("This command can only be used in guilds.", ephemeral=True)
            return
        state = self._get_state(guild.id)
        success = state.queue.move(source - 1, destination - 1)
        if success:
            await interaction.followup.send("Track moved.")
        else:
            await interaction.followup.send("Invalid indices provided.", ephemeral=True)

    @nextcord.slash_command(name="shuffle", description="Shuffle the queue")
    async def shuffle(self, interaction: nextcord.Interaction) -> None:
        await interaction.response.defer()
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("This command can only be used in guilds.", ephemeral=True)
            return
        state = self._get_state(guild.id)
        state.queue.shuffle()
        await interaction.followup.send("Queue shuffled.")

    @nextcord.slash_command(name="replay", description="Replay the last played track")
    async def replay(self, interaction: nextcord.Interaction) -> None:
        await interaction.response.defer()
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("This command can only be used in guilds.", ephemeral=True)
            return
        state = self._get_state(guild.id)
        replayed = state.queue.replay_last()
        if not replayed:
            await interaction.followup.send("Nothing to replay.", ephemeral=True)
            return
        await interaction.followup.send(
            embed=self.embed_factory.queued(
                replayed,
                position=1,
                eta_ms=self._calculate_eta_ms(guild.id),
            )
        )
        await self._ensure_playing(guild.id)

    @nextcord.slash_command(name="ytcheck", description="Show YouTube stack diagnostics")
    async def ytcheck(self, interaction: nextcord.Interaction) -> None:
        await interaction.response.defer()
        try:
            report = await self.diagnostics.collect()
        except Exception as exc:  # pragma: no cover - diagnostics failure
            await interaction.followup.send(f"Diagnostics failed: {exc}", ephemeral=True)
            return
        fields = [
            f"Lavalink latency: {report.lavalink_latency_ms} ms",
            f"Lavalink version: {report.lavalink_version}",
            f"youtube-source: {report.youtube_plugin_version}",
            f"yt-dlp: {report.yt_dlp_version}",
        ]
        age = report.cookie_file_age_seconds
        if age is not None:
            fields.append(f"Cookie file age: {int(age)}s")
        fields.append(f"Metrics: {report.metrics}")
        embed = nextcord.Embed(title="YouTube diagnostics", description="\n".join(fields))
        await interaction.followup.send(embed=embed)

    # ------------------------------------------------------------------
    # Mafic event listeners
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_track_end(self, event: mafic.TrackEndEvent) -> None:  # pragma: no cover - integration
        guild_id = event.player.guild.id
        state = self._states.get(guild_id)
        if not state:
            return
        state.now_playing = None
        await self._ensure_playing(guild_id)

    @commands.Cog.listener()
    async def on_track_exception(self, event: mafic.TrackExceptionEvent) -> None:  # pragma: no cover
        guild_id = event.player.guild.id
        state = self._states.get(guild_id)
        if not state:
            return
        self.metrics.incr_failed()
        state.now_playing = None
        await self._ensure_playing(guild_id)

    @commands.Cog.listener()
    async def on_track_stuck(self, event: mafic.TrackStuckEvent) -> None:  # pragma: no cover
        guild_id = event.player.guild.id
        state = self._states.get(guild_id)
        if not state:
            return
        self.metrics.incr_failed()
        state.now_playing = None
        await self._ensure_playing(guild_id)


def setup(bot: commands.Bot) -> None:
    bot.add_cog(Music(bot))

