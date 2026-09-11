"""Nextcord music cog backed by Lavalink v4 with yt-dlp fallback."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, Optional
from urllib.parse import urlsplit

import nextcord
from cachetools import TTLCache

try:
    import mafic
except Exception:
    mafic = None
from nextcord.ext import commands

from elbot.config import get_lavalink_connection_info
from elbot.music import (
    CookieManager,
    DiagnosticsService,
    EmbedFactory,
    FallbackPlayer,
    LavalinkAudioBackend,
    MusicQueue,
    PlaybackMetrics,
    QueuedTrack,
    QueuePaginator,
    SearchCache,
    TrackLoadFailure,
    configure_json_logging,
)
from elbot.utils import safe_reply

_LOGGING_INITIALISED = False


def _ensure_logging() -> None:
    global _LOGGING_INITIALISED
    if not _LOGGING_INITIALISED:
        configure_json_logging()
        _LOGGING_INITIALISED = True


def _lavalink_config() -> tuple[str, int, str, bool]:
    return get_lavalink_connection_info()


@dataclass
class GuildState:
    queue: MusicQueue = field(default_factory=MusicQueue)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    playback_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    voice_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    now_playing: Optional[QueuedTrack] = None
    player: Optional[object] = None
    last_channel_id: Optional[int] = None
    now_playing_message: Optional[nextcord.Message] = None
    now_playing_view: Optional[nextcord.ui.View] = None
    controller_task: Optional[asyncio.Task] = None
    # Set by on_track_exception to prevent on_track_end from advancing
    # while a fallback is being resolved (race condition fix).
    _fallback_pending: bool = False
    # Lavalink can emit track_end *before* track_exception for the same
    # failure; keep the just-ended entry briefly so the exception handler
    # can still resolve a fallback for it.
    last_ended: Optional[QueuedTrack] = None
    last_ended_at: float = 0.0
    loop_mode: str = "off"
    autoplay: bool = False
    autoplay_history: list[str] = field(default_factory=list)
    volume: int = 100
    idle_task: Optional[asyncio.Task] = None
    pending_end_task: Optional[asyncio.Task] = None
    suppressed_track_keys: set[str] = field(default_factory=set)
    playback_started_at: float = 0.0


class MusicControls(nextcord.ui.View):
    """Persistent controls attached to Elbot's now-playing message."""

    def __init__(self, cog: "Music", guild_id: int) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id

        dashboard_url = os.getenv("ELBOT_MUSIC_DASHBOARD_URL", "").strip()
        parsed = urlsplit(dashboard_url) if dashboard_url else None
        if parsed and parsed.scheme in {"http", "https"} and parsed.netloc:
            self.remove_item(self.queue_button)
            self.add_item(
                nextcord.ui.Button(
                    label="Dashboard",
                    emoji="📊",
                    style=nextcord.ButtonStyle.link,
                    url=dashboard_url,
                    row=0,
                )
            )
        self.sync_from_state()

    async def on_error(
        self,
        error: Exception,
        item: nextcord.ui.Item,
        interaction: nextcord.Interaction,
    ) -> None:
        self.cog.logger.error(
            "Music controller action failed",
            extra={
                "guild_id": self.guild_id,
                "control": getattr(item, "custom_id", None),
            },
            exc_info=error,
        )
        try:
            await safe_reply(
                interaction,
                "That control failed. Please try again.",
                ephemeral=True,
            )
        except Exception:
            pass

    def sync_from_state(self) -> None:
        state = self.cog._states.get(self.guild_id)
        if state is None:
            return
        paused = bool(getattr(state.player, "paused", False))
        self.pause_button.label = "Resume" if paused else "Pause"
        self.autoplay_button.label = f"AutoPlay: {'On' if state.autoplay else 'Off'}"
        self.autoplay_button.style = (
            nextcord.ButtonStyle.success
            if state.autoplay
            else nextcord.ButtonStyle.secondary
        )

    async def interaction_check(self, interaction: nextcord.Interaction) -> bool:
        guild = interaction.guild
        if guild is None or guild.id != self.guild_id:
            await safe_reply(
                interaction,
                "These controls belong to another server.",
                ephemeral=True,
            )
            return False
        state = self.cog._states.get(self.guild_id)
        if state is None or state.player is None or state.now_playing is None:
            await safe_reply(
                interaction,
                "Nothing is playing right now.",
                ephemeral=True,
            )
            return False
        control_error = self.cog._control_error(interaction, state)
        if control_error:
            await safe_reply(interaction, control_error, ephemeral=True)
            return False
        return True

    @nextcord.ui.button(
        label="Pause",
        emoji="⏯️",
        style=nextcord.ButtonStyle.secondary,
        custom_id="elbot:music:pause",
        row=0,
    )
    async def pause_button(
        self, _: nextcord.ui.Button, interaction: nextcord.Interaction
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        state = self.cog._states[self.guild_id]
        if getattr(state.player, "paused", False):
            await state.player.resume()
            message = "Playback resumed."
        else:
            await state.player.pause()
            message = "Playback paused."
        await self.cog._refresh_now_playing(self.guild_id)
        await safe_reply(interaction, message, ephemeral=True)

    @nextcord.ui.button(
        label="Skip",
        emoji="⏭️",
        style=nextcord.ButtonStyle.secondary,
        custom_id="elbot:music:skip",
        row=0,
    )
    async def skip_button(
        self, _: nextcord.ui.Button, interaction: nextcord.Interaction
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        state = self.cog._states[self.guild_id]
        _, message = await self.cog._skip_current(self.guild_id, state)
        await safe_reply(interaction, message, ephemeral=True)

    @nextcord.ui.button(
        label="Stop",
        emoji="⏹️",
        style=nextcord.ButtonStyle.danger,
        custom_id="elbot:music:stop",
        row=0,
    )
    async def stop_button(
        self, _: nextcord.ui.Button, interaction: nextcord.Interaction
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        await self.cog._stop(self.guild_id)
        await safe_reply(
            interaction,
            "Playback stopped and queue cleared.",
            ephemeral=True,
        )

    @nextcord.ui.button(
        label="AutoPlay: Off",
        emoji="🔄",
        style=nextcord.ButtonStyle.secondary,
        custom_id="elbot:music:autoplay",
        row=0,
    )
    async def autoplay_button(
        self, _: nextcord.ui.Button, interaction: nextcord.Interaction
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        state = self.cog._states[self.guild_id]
        state.autoplay = not state.autoplay
        await self.cog._refresh_now_playing(self.guild_id)
        await safe_reply(
            interaction,
            f"AutoPlay turned **{'on' if state.autoplay else 'off'}**.",
            ephemeral=True,
        )

    @nextcord.ui.button(
        label="Queue",
        emoji="📋",
        style=nextcord.ButtonStyle.secondary,
        custom_id="elbot:music:queue",
        row=0,
    )
    async def queue_button(
        self, _: nextcord.ui.Button, interaction: nextcord.Interaction
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        await self.cog._send_controller_queue(interaction, self.guild_id)

    @nextcord.ui.button(
        label="Love this",
        emoji="👍",
        style=nextcord.ButtonStyle.secondary,
        custom_id="elbot:music:love",
        row=1,
    )
    async def love_button(
        self, _: nextcord.ui.Button, interaction: nextcord.Interaction
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        state = self.cog._states[self.guild_id]
        likes, _ = self.cog._record_feedback(
            state.now_playing, getattr(interaction.user, "id", 0), loved=True
        )
        await self.cog._refresh_now_playing(self.guild_id)
        await safe_reply(
            interaction,
            f"Saved your vote · **{likes}** like{'s' if likes != 1 else ''}.",
            ephemeral=True,
        )

    @nextcord.ui.button(
        label="Not for me",
        emoji="👎",
        style=nextcord.ButtonStyle.secondary,
        custom_id="elbot:music:dislike",
        row=1,
    )
    async def dislike_button(
        self, _: nextcord.ui.Button, interaction: nextcord.Interaction
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        state = self.cog._states[self.guild_id]
        self.cog._record_feedback(
            state.now_playing,
            getattr(interaction.user, "id", 0),
            loved=False,
        )
        _, message = await self.cog._skip_current(self.guild_id, state)
        await safe_reply(interaction, message, ephemeral=True)

    @nextcord.ui.button(
        label="What's next?",
        emoji="🔮",
        style=nextcord.ButtonStyle.secondary,
        custom_id="elbot:music:next",
        row=1,
    )
    async def next_button(
        self, _: nextcord.ui.Button, interaction: nextcord.Interaction
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        state = self.cog._states[self.guild_id]
        next_track = state.queue.peek()
        if next_track:
            await safe_reply(
                interaction,
                embed=self.cog.embed_factory.queued(
                    next_track,
                    position=1,
                    eta_ms=0,
                ),
                ephemeral=True,
            )
            return
        message = (
            "The queue is empty. AutoPlay will choose a related track."
            if state.autoplay
            else "The queue is empty."
        )
        await safe_reply(interaction, message, ephemeral=True)


class Music(commands.Cog):
    """Slash command music cog with resilient fallback playback."""

    def __init__(self, bot: commands.Bot) -> None:
        _ensure_logging()
        self.bot = bot
        self._backend_lock = threading.Lock()
        self.logger = logging.getLogger("elbot.music")
        # Defer creating the Lavalink backend until it's actually needed so
        # importing the cog doesn't require the 'mafic' package to be
        # installed during tests.
        self._backend = None
        self.metrics = PlaybackMetrics()
        self.cookies = CookieManager()
        self.search_cache = SearchCache()
        self._autocomplete_cache: "TTLCache[str, Dict[str, str]]" = TTLCache(
            maxsize=256, ttl=300
        )
        self.fallback = None
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
        self._track_feedback: Dict[str, Dict[str, set[int]]] = {}

    @property
    def backend(self) -> LavalinkAudioBackend:
        if self._backend is None:
            with self._backend_lock:
                if self._backend is None:
                    self._backend = LavalinkAudioBackend(self.bot)
                    # initialize fallback that relies on backend
                    self.fallback = FallbackPlayer(
                        self._backend,
                        cookies=self.cookies,
                        metrics=self.metrics,
                        search_cache=self.search_cache,
                    )
        return self._backend

    # ------------------------------------------------------------------
    # Cog lifecycle
    # ------------------------------------------------------------------
    async def cog_load(self) -> None:  # type: ignore[override]
        # Pre-initialize backend in background to avoid lazy loading delays
        async def _init_backend():
            try:
                await self.backend.wait_ready()
                self.logger.info("Music backend pre-initialized successfully")
            except Exception as e:
                self.logger.warning("Failed to pre-initialize backend: %s", e)

        # Don't wait for this - let it run in background
        self.bot.loop.create_task(_init_backend())

    async def _cog_cleanup(self) -> None:
        for guild_id, state in list(self._states.items()):
            await self._disconnect(guild_id, state)
        backend = self._backend
        if backend is not None:
            await backend.close()
            self._backend = None
            self.fallback = None
        await self.diagnostics.close()

    def cog_unload(self) -> None:  # type: ignore[override]
        async def _run_cleanup() -> None:
            try:
                await self._cog_cleanup()
            except Exception:  # pragma: no cover - defensive cleanup
                self.logger.exception("Music cog cleanup failed during unload")

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(_run_cleanup())
        else:
            try:
                loop.create_task(_run_cleanup())
            except RuntimeError:
                asyncio.run(_run_cleanup())

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------
    def _get_state(self, guild_id: int) -> GuildState:
        if guild_id not in self._states:
            self._states[guild_id] = GuildState()
        return self._states[guild_id]

    @staticmethod
    def _handle_identity(handle: object) -> str:
        title = " ".join(str(getattr(handle, "title", "")).casefold().split())
        author = " ".join(str(getattr(handle, "author", "")).casefold().split())
        return f"{author}\0{title}"

    @classmethod
    def _track_identity(cls, track: QueuedTrack) -> str:
        return cls._handle_identity(track.handle)

    def _record_feedback(
        self,
        track: Optional[QueuedTrack],
        user_id: int,
        *,
        loved: bool,
    ) -> tuple[int, int]:
        if track is None:
            return 0, 0
        feedback = self._track_feedback.setdefault(
            self._track_identity(track),
            {"likes": set(), "dislikes": set()},
        )
        selected = feedback["likes" if loved else "dislikes"]
        opposite = feedback["dislikes" if loved else "likes"]
        selected.add(user_id)
        opposite.discard(user_id)
        return len(feedback["likes"]), len(feedback["dislikes"])

    def _feedback_counts(self, track: QueuedTrack) -> tuple[int, int]:
        feedback = self._track_feedback.get(self._track_identity(track))
        if not feedback:
            return 0, 0
        return len(feedback["likes"]), len(feedback["dislikes"])

    def _now_playing_embed(self, guild_id: int) -> Optional[nextcord.Embed]:
        state = self._states.get(guild_id)
        if state is None or state.now_playing is None:
            return None
        player = state.player
        position = int(getattr(player, "position", 0) or 0)
        channel = getattr(player, "channel", None)
        voice_channel = getattr(channel, "mention", None)
        likes, _ = self._feedback_counts(state.now_playing)
        return self.embed_factory.now_playing(
            state.now_playing,
            position=position,
            queue_size=len(state.queue),
            volume=state.volume,
            loop_mode=state.loop_mode,
            voice_channel=voice_channel,
            paused=bool(getattr(player, "paused", False)),
            autoplay=state.autoplay,
            likes=likes,
        )

    async def _refresh_now_playing(self, guild_id: int) -> None:
        state = self._states.get(guild_id)
        if state is None or state.now_playing_message is None:
            return
        embed = self._now_playing_embed(guild_id)
        if embed is None:
            return
        view = state.now_playing_view
        if isinstance(view, MusicControls):
            view.sync_from_state()
        try:
            await state.now_playing_message.edit(embed=embed, view=view)
        except Exception as exc:
            self.logger.debug(
                "Could not refresh now-playing controller",
                extra={"guild_id": guild_id, "error": self._safe_log_value(str(exc))},
            )

    def _start_controller_updates(self, guild_id: int, state: GuildState) -> None:
        task = state.controller_task
        if task is not None and not task.done():
            task.cancel()
        interval = self._env_float(
            "ELBOT_NOW_PLAYING_UPDATE_INTERVAL", 15.0, minimum=5.0
        )

        async def update_progress() -> None:
            try:
                while self._states.get(guild_id) is state:
                    await asyncio.sleep(interval)
                    if state.now_playing is None or state.now_playing_message is None:
                        return
                    await self._refresh_now_playing(guild_id)
            except asyncio.CancelledError:
                return

        state.controller_task = self.bot.loop.create_task(update_progress())

    async def _send_controller_queue(
        self,
        interaction: nextcord.Interaction,
        guild_id: int,
    ) -> None:
        state = self._states.get(guild_id)
        if state is None:
            await safe_reply(interaction, "The queue is empty.", ephemeral=True)
            return
        tracks = state.queue.snapshot()
        embed = self.embed_factory.queue_page(
            tracks[:8],
            page=0,
            per_page=8,
            total=len(tracks),
            now_playing=state.now_playing,
        )
        await safe_reply(interaction, embed=embed, ephemeral=True)

    def _resolve_mafic(self):
        global mafic
        if mafic is None:
            os.environ.setdefault("MAFIC_LIBRARY", "nextcord")
            os.environ.setdefault("MAFIC_IGNORE_LIBRARY_CHECK", "1")
            try:
                import mafic as _mafic
            except Exception as exc:
                raise RuntimeError("mafic library is required for music playback") from exc
            mafic = _mafic
        return mafic

    async def _disconnect(self, guild_id: int, state: GuildState) -> None:
        current_task = asyncio.current_task()
        for task in (state.idle_task, state.pending_end_task):
            if task is not None and task is not current_task and not task.done():
                task.cancel()
        state.idle_task = None
        state.pending_end_task = None
        state.queue.clear()
        state.now_playing = None
        state.playback_started_at = 0.0
        if state.player:
            try:
                await self._release_voice(state.player)
            except Exception:  # pragma: no cover - defensive cleanup
                pass
        await self._clear_now_playing_message(state)
        self._states.pop(guild_id, None)

    async def _ensure_voice(
        self, interaction: nextcord.Interaction
    ) -> tuple[Optional[mafic.Player], Optional[str]]:
        if interaction.guild is None:
            return None, "This command can only be used in guilds."
        state = self._get_state(interaction.guild.id)
        async with state.voice_lock:
            return await self._ensure_voice_locked(interaction)

    async def _release_voice(self, player: object) -> None:
        """Bound network teardown and always release a failed local registration."""
        try:
            await asyncio.wait_for(player.disconnect(force=True), timeout=5.0)
        except Exception as exc:
            self.logger.warning(
                "Voice teardown failed; releasing local registration",
                extra={"error_type": type(exc).__name__},
            )
            player.cleanup()
        except asyncio.CancelledError:
            player.cleanup()
            raise

    async def _connect_voice(self, channel, player_cls, timeout: float):
        if os.getenv("ELBOT_VOICE_TRANSPORT", "lavalink") == "bridge":
            from elbot.live.bridge_player import BridgePlayer

            player_cls = BridgePlayer
            timeout = max(timeout, 30.0)
        # Capture exactly the client created by this attempt, even when
        # Nextcord raises before channel.connect returns it.
        player = None

        def create_player(client, target):
            nonlocal player
            player = player_cls(client, target)
            return player

        try:
            return await asyncio.wait_for(
                channel.connect(cls=create_player, timeout=timeout, reconnect=True),
                timeout=timeout,
            )
        except (Exception, asyncio.CancelledError) as exc:
            self.logger.warning(
                "Voice handshake failed",
                extra={
                    "error_type": type(exc).__name__,
                    "timeout_s": timeout,
                    **(self._player_connect_diagnostics(player) if player else {}),
                },
            )
            if player is not None:
                await self._release_voice(player)
            raise

    async def _ensure_voice_locked(
        self, interaction: nextcord.Interaction
    ) -> tuple[Optional[mafic.Player], Optional[str]]:
        user = interaction.user
        if user is None or not isinstance(user, nextcord.Member) or user.voice is None:
            return None, "You must join a voice channel first."
        guild = interaction.guild
        if guild is None:
            return None, "This command can only be used in guilds."

        if not await self.backend.wait_ready():
            return None, "Lavalink node is not ready."

        mafic_lib = self._resolve_mafic()
        connect_timeout = self._env_float(
            "ELBOT_PLAYER_CONNECT_TIMEOUT", 8.0, minimum=0.0
        )
        connect_timeout = max(connect_timeout, 1.0)

        state = self._get_state(guild.id)
        voice = guild.voice_client
        if voice and not isinstance(voice, mafic_lib.Player):
            try:
                await self._release_voice(voice)
            except Exception:
                pass
            voice = None
            state.player = None

        if voice and not self._player_is_connected(voice):
            self.logger.warning(
                "Existing guild voice client is disconnected; forcing reconnect",
                extra={
                    "guild_id": guild.id,
                    **self._player_connect_diagnostics(voice),
                },
            )
            try:
                await self._release_voice(voice)
            except Exception:
                pass
            voice = None
            state.player = None

        target_channel = user.voice.channel
        if voice and voice.channel != target_channel:
            if state.now_playing is not None or len(state.queue) > 0:
                channel_mention = getattr(voice.channel, "mention", "another channel")
                return None, f"I'm already playing music in {channel_mention}."
            try:
                await voice.move_to(target_channel)
            except Exception:
                await self._release_voice(voice)
                voice = None
                state.player = None

        if voice is None:
            try:
                voice = await self._connect_voice(target_channel, mafic_lib.Player, connect_timeout)
            except Exception as exc:
                self.logger.error(
                    "Voice connection failed",
                    extra={"guild_id": guild.id, "voice_channel_id": target_channel.id},
                    exc_info=exc,
                )
                return None, "Could not join your voice channel."

        state.player = voice
        state.last_channel_id = interaction.channel_id
        try:
            connected = await self._wait_for_player_connection(voice, connect_timeout)
        except asyncio.CancelledError:
            await self._release_voice(voice)
            state.player = None
            raise
        if not connected:
            self.logger.warning(
                "Voice connect returned but player is still not connected",
                extra={
                    "guild_id": guild.id,
                    "voice_channel_id": target_channel.id,
                    "timeout_s": connect_timeout,
                    **self._player_connect_diagnostics(voice),
                },
            )
            try:
                await self._release_voice(voice)
            except Exception:
                pass
            state.player = None
            return (
                None,
                "Could not establish voice connection. Please reconnect to voice and try again.",
            )
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

    async def _enqueue_autoplay_track(
        self,
        guild_id: int,
        state: GuildState,
        previous: QueuedTrack,
    ) -> bool:
        """Find one related, recently-unplayed track when AutoPlay is enabled."""

        query = f"{previous.handle.author} {previous.handle.title} mix"
        try:
            candidates = await asyncio.wait_for(
                self.backend.resolve_tracks(query, prefer_search=True),
                timeout=8.0,
            )
        except Exception as exc:
            self.logger.warning(
                "AutoPlay could not load a related track",
                extra={
                    "guild_id": guild_id,
                    "track_title": previous.handle.title,
                    "error": self._safe_log_value(str(exc)),
                },
            )
            return False

        recent = set(state.autoplay_history)
        selected = next(
            (
                candidate
                for candidate in candidates
                if self._handle_identity(candidate) not in recent
            ),
            None,
        )
        if selected is None:
            return False
        bot_user = self.bot.user
        entry = QueuedTrack(
            id=uuid.uuid4().hex,
            handle=selected,
            query=query,
            channel_id=previous.channel_id,
            requested_by=getattr(bot_user, "id", 0),
            requester_display="AutoPlay",
        )
        state.queue.add(entry)
        self.logger.info(
            "AutoPlay queued a related track",
            extra=self._track_log_context(guild_id, entry),
        )
        return True

    def _track_log_context(
        self,
        guild_id: int,
        entry: Optional[QueuedTrack],
        track: Optional[mafic.Track] = None,
    ) -> dict[str, object]:
        context: dict[str, object] = {"guild_id": guild_id}
        handle = entry.handle if entry else None

        if entry is not None:
            context["is_fallback"] = entry.is_fallback
            if entry.fallback_source:
                context["fallback_source"] = self._safe_log_value(entry.fallback_source)
            context["track_query"] = self._safe_log_value(entry.query)

        if handle is not None:
            context.update(
                {
                    "track_title": handle.title,
                    "track_author": handle.author,
                    "track_source": handle.source,
                    "track_duration": handle.duration,
                    "track_uri": self._safe_log_value(handle.uri),
                }
            )
            try:
                identifier = getattr(handle.track, "identifier", None)
                if identifier:
                    context.setdefault(
                        "track_identifier", self._safe_log_value(identifier)
                    )
            except AttributeError:
                pass

        if track is not None:
            context.setdefault("track_title", getattr(track, "title", None))
            context.setdefault("track_author", getattr(track, "author", None))
            context.setdefault("track_source", getattr(track, "source", None))
            context.setdefault("track_duration", getattr(track, "length", None))
            context.setdefault(
                "track_uri", self._safe_log_value(getattr(track, "uri", None))
            )
            identifier = getattr(track, "identifier", None)
            if identifier:
                context.setdefault(
                    "track_identifier", self._safe_log_value(identifier)
                )

        return context

    @staticmethod
    def _safe_log_value(value: object, *, limit: int = 160) -> object:
        """Keep useful source context without logging signed media URLs."""

        if not isinstance(value, str):
            return value
        text = value.strip()
        if not text:
            return text
        if "://" in text:
            parsed = urlsplit(text)
            if parsed.hostname:
                return f"{parsed.scheme}://{parsed.hostname}"
        if len(text) > limit:
            return f"{text[:limit]}..."
        return text

    @staticmethod
    def _track_key(track: object) -> Optional[str]:
        if track is None:
            return None
        for name in ("encoded", "id", "identifier"):
            value = getattr(track, name, None)
            if isinstance(value, str) and value:
                return f"{name}:{value}"
        info = getattr(track, "info", None)
        if isinstance(info, dict):
            value = info.get("identifier")
            if value:
                return f"identifier:{value}"
        return f"object:{id(track)}"

    def _suppress_next_track_end(self, state: GuildState, track: object) -> None:
        key = self._track_key(track)
        if not key:
            return
        state.suppressed_track_keys.add(key)
        self.bot.loop.call_later(5.0, state.suppressed_track_keys.discard, key)

    @staticmethod
    def _normalise_end_reason(reason: object) -> str:
        value = getattr(reason, "name", None) or getattr(reason, "value", None) or reason
        text = str(value or "UNKNOWN").upper()
        return text.rsplit(".", 1)[-1]

    def _control_error(
        self, interaction: nextcord.Interaction, state: GuildState
    ) -> Optional[str]:
        """Require listeners to control the player from its voice channel."""

        player_channel = getattr(state.player, "channel", None)
        if player_channel is None:
            return None
        user = interaction.user
        if not isinstance(user, nextcord.Member):
            return "This command can only be used in a server voice channel."
        permissions = getattr(user, "guild_permissions", None)
        if permissions and (
            getattr(permissions, "administrator", False)
            or getattr(permissions, "manage_guild", False)
        ):
            return None
        user_channel = getattr(getattr(user, "voice", None), "channel", None)
        if user_channel != player_channel:
            mention = getattr(player_channel, "mention", "the bot's voice channel")
            return f"Join {mention} to control playback."
        return None

    @staticmethod
    def _parse_seek_position(value: str) -> Optional[int]:
        parts = value.strip().split(":")
        if not parts or len(parts) > 3:
            return None
        try:
            numbers = [int(part) for part in parts]
        except ValueError:
            return None
        if any(number < 0 for number in numbers):
            return None
        if len(numbers) > 1 and any(number >= 60 for number in numbers[1:]):
            return None
        seconds = 0
        for number in numbers:
            seconds = seconds * 60 + number
        return seconds * 1000

    @staticmethod
    def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
        raw = os.getenv(name, "").strip()
        if not raw:
            return default
        try:
            value = int(raw)
        except ValueError:
            return default
        return max(minimum, value)

    @staticmethod
    def _env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
        raw = os.getenv(name, "").strip()
        if not raw:
            return default
        try:
            value = float(raw)
        except ValueError:
            return default
        return max(minimum, value)

    def _player_is_connected(self, player: object) -> bool:
        for attr_name in ("is_connected", "connected"):
            value = getattr(player, attr_name, None)
            if callable(value):
                try:
                    value = value()
                except Exception:
                    value = None
            if isinstance(value, bool):
                if value:
                    return True
            elif hasattr(value, "is_set"):
                try:
                    if value.is_set():
                        return True
                except Exception:
                    pass
        return False

    def _player_connection_context(self, player: object) -> dict[str, object]:
        channel = getattr(player, "channel", None)
        guild = getattr(player, "guild", None)
        return {
            "player_type": type(player).__name__,
            "player_connected": self._player_is_connected(player),
            "voice_channel_id": getattr(channel, "id", None),
            "guild_id": getattr(guild, "id", None),
        }

    @staticmethod
    def _event_is_set(value: object) -> Optional[bool]:
        if value is None:
            return None
        checker = getattr(value, "is_set", None)
        if not callable(checker):
            return None
        try:
            return bool(checker())
        except Exception:
            return None

    def _player_connect_diagnostics(self, player: object) -> dict[str, object]:
        context = self._player_connection_context(player)
        context.update(
            {
                "mafic_session_id": getattr(player, "_session_id", None),
                "mafic_endpoint": getattr(player, "_endpoint", None)
                or getattr(player, "endpoint", None),
                "voice_state_event_set": self._event_is_set(
                    getattr(player, "_voice_state_update_event", None)
                ),
                "voice_server_event_set": self._event_is_set(
                    getattr(player, "_voice_server_update_event", None)
                ),
                "node_player_ready_event_set": self._event_is_set(
                    getattr(player, "_node_player_ready_event", None)
                ),
            }
        )
        return context

    async def _wait_for_player_connection(self, player: object, timeout_s: float) -> bool:
        timeout_s = max(0.0, timeout_s)
        if self._player_is_connected(player):
            return True
        if timeout_s == 0:
            return False
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        while loop.time() < deadline:
            if self._player_is_connected(player):
                return True
            await asyncio.sleep(0.1)
        return self._player_is_connected(player)

    async def _reconnect_player(
        self,
        guild_id: int,
        state: GuildState,
        player: object,
        mafic_lib,
        connect_timeout: float,
    ) -> Optional[object]:
        async with state.voice_lock:
            if state.player is not player:
                return state.player
            return await self._reconnect_player_locked(
                guild_id, state, player, mafic_lib, connect_timeout
            )

    async def _reconnect_player_locked(
        self, guild_id: int, state: GuildState, player: object,
        mafic_lib, connect_timeout: float,
    ) -> Optional[object]:
        channel = getattr(player, "channel", None)
        if channel is None:
            self.logger.warning(
                "Reconnect skipped: player has no bound channel",
                extra={"guild_id": guild_id},
            )
            return None
        try:
            await self._release_voice(player)
        except Exception:
            pass
        try:
            state.player = None
            new_player = await self._connect_voice(channel, mafic_lib.Player, connect_timeout)
        except Exception as exc:
            self.logger.warning(
                "Voice reconnect attempt failed",
                extra={
                    "guild_id": guild_id,
                    "voice_channel_id": getattr(channel, "id", None),
                    **self._player_connect_diagnostics(player),
                },
                exc_info=exc,
            )
            return None
        state.player = new_player
        self.logger.info(
            "Reconnected player to voice channel",
            extra={
                "guild_id": guild_id,
                "voice_channel_id": getattr(channel, "id", None),
            },
        )
        return new_player

    async def _notify_playback_failure(self, guild_id: int, track: QueuedTrack) -> None:
        state = self._get_state(guild_id)
        channel_id = track.channel_id or state.last_channel_id
        if not channel_id:
            return
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except Exception:
                return
        if isinstance(channel, nextcord.abc.Messageable):
            try:
                await channel.send(
                    embed=self.embed_factory.failure(
                        f"Could not play **{track.handle.title}**: failed to connect to voice channel."
                    )
                )
            except Exception:
                pass

    async def _begin_playback(self, guild_id: int) -> None:
        state = self._get_state(guild_id)
        self._cancel_idle_disconnect(state)
        player = state.player
        if player is None:
            return
        if state.now_playing is not None:
            return
        next_track = state.queue.pop_next()
        if not next_track:
            return
        state.now_playing = next_track
        mafic_lib = self._resolve_mafic()
        max_attempts = self._env_int("ELBOT_PLAYER_CONNECT_RETRIES", 20, minimum=1)
        retry_delay = self._env_float(
            "ELBOT_PLAYER_CONNECT_RETRY_DELAY", 0.75, minimum=0.1
        )
        connect_timeout = self._env_float(
            "ELBOT_PLAYER_CONNECT_TIMEOUT", 8.0, minimum=0.0
        )
        reconnect_attempt = min(
            self._env_int("ELBOT_PLAYER_RECONNECT_ATTEMPT", 6, minimum=1),
            max_attempts,
        )
        if not await self._wait_for_player_connection(player, connect_timeout):
            context = self._player_connection_context(player)
            context["timeout_s"] = connect_timeout
            context["guild_id"] = guild_id
            self.logger.warning(
                "Player still not connected after warmup window", extra=context
            )
        reconnect_done = False
        for attempt in range(max_attempts):
            latest_player = state.player
            if latest_player is None:
                self.metrics.incr_failed()
                self.logger.error(
                    "Playback aborted: no active player in state",
                    extra={"guild_id": guild_id},
                )
                state.queue.add_next(next_track)
                state.now_playing = None
                state.playback_started_at = 0.0
                return
            if latest_player is not player:
                player = latest_player
            try:
                await player.play(next_track.handle.track, volume=state.volume)
                # Lavalink v4 returns a canonical copy of the track it accepted.
                # HTTP sources in particular can receive a different encoded ID
                # from the one returned by the earlier load-tracks request.  End
                # events contain that canonical copy, so retain it for reliable
                # event matching and automatic queue advancement.
                canonical_track = getattr(player, "current", None)
                if canonical_track is not None:
                    next_track.handle.track = canonical_track
                state.player = player
                state.playback_started_at = time.monotonic()
                identity = self._track_identity(next_track)
                if not state.autoplay_history or state.autoplay_history[-1] != identity:
                    state.autoplay_history.append(identity)
                    del state.autoplay_history[:-20]
                context = self._track_log_context(guild_id, next_track)
                self.logger.info(
                    "Playback started: %s (%s)",
                    next_track.handle.title,
                    next_track.handle.source,
                    extra=context,
                )
                self.metrics.incr_started()
                await self._announce_now_playing(guild_id)
                return
            except mafic_lib.PlayerNotConnected:
                if attempt >= max_attempts - 1:
                    break
                context = self._player_connection_context(player)
                context["attempt"] = attempt + 1
                context["max_attempts"] = max_attempts
                context["guild_id"] = guild_id
                self.logger.warning(
                    "Player not connected, retrying playback", extra=context
                )
                if not reconnect_done and attempt + 1 >= reconnect_attempt:
                    reconnect_done = True
                    reconnected = await self._reconnect_player(
                        guild_id, state, player, mafic_lib, connect_timeout
                    )
                    if reconnected is None:
                        break
                    player = reconnected
                    if not await self._wait_for_player_connection(
                        player, connect_timeout
                    ):
                        reconnect_context = self._player_connection_context(player)
                        reconnect_context["guild_id"] = guild_id
                        reconnect_context["timeout_s"] = connect_timeout
                        self.logger.warning(
                            "Reconnected player still not connected after warmup window",
                            extra=reconnect_context,
                        )
                        break
                await asyncio.sleep(retry_delay)
            except Exception as exc:  # pragma: no cover - network errors
                self.metrics.incr_failed()
                self.logger.error("Failed to start playback", exc_info=exc)
                state.now_playing = None
                state.playback_started_at = 0.0
                await self._begin_playback(guild_id)
                return
        # All retry attempts exhausted or early abort due to reconnect failure
        self.metrics.incr_failed()
        context = self._player_connection_context(player)
        context["max_attempts"] = max_attempts
        context["guild_id"] = guild_id
        self.logger.error(
            "Player failed to connect after %d retries, giving up",
            max_attempts,
            extra=context,
        )
        failed_player = state.player
        if failed_player is not None and not self._player_is_connected(failed_player):
            try:
                await self._release_voice(failed_player)
            except Exception:
                pass
            state.player = None
        state.queue.add_next(next_track)
        state.now_playing = None
        state.playback_started_at = 0.0
        await self._notify_playback_failure(guild_id, next_track)

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
            # If a queued message exists and we can fetch it, edit it into now-playing
            qm_id = getattr(track, "queued_message_id", None)
            if qm_id:
                try:
                    queued_msg = await channel.fetch_message(qm_id)
                    await self._clear_now_playing_message(state)
                    view = MusicControls(self, guild_id)
                    embed = self._now_playing_embed(guild_id)
                    await queued_msg.edit(embed=embed, view=view)
                    state.now_playing_message = queued_msg
                    state.now_playing_view = view
                    self._start_controller_updates(guild_id, state)
                    return
                except Exception:
                    # fetching/editing failed; fall back to sending a new message
                    pass
            await self._clear_now_playing_message(state)
            view = MusicControls(self, guild_id)
            embed = self._now_playing_embed(guild_id)
            message = await channel.send(embed=embed, view=view)
            state.now_playing_message = message
            state.now_playing_view = view
            self._start_controller_updates(guild_id, state)

    async def _clear_now_playing_message(self, state: GuildState) -> None:
        task = state.controller_task
        state.controller_task = None
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
        view = state.now_playing_view
        state.now_playing_view = None
        if view is not None:
            view.stop()
        message = state.now_playing_message
        if not message:
            return
        state.now_playing_message = None
        try:
            await message.delete()
        except Exception:
            pass

    @staticmethod
    def _cancel_idle_disconnect(state: GuildState) -> None:
        task = state.idle_task
        state.idle_task = None
        if task is not None and not task.done():
            task.cancel()

    def _schedule_idle_disconnect(self, guild_id: int, state: GuildState) -> None:
        self._cancel_idle_disconnect(state)
        timeout = self._env_float("ELBOT_MUSIC_IDLE_TIMEOUT", 180.0, minimum=0.0)
        if timeout <= 0:
            return

        async def disconnect_when_idle() -> None:
            try:
                await asyncio.sleep(timeout)
                current = self._states.get(guild_id)
                if current is not state:
                    return
                if state.now_playing is None and len(state.queue) == 0:
                    self.logger.info(
                        "Disconnecting idle music player",
                        extra={"guild_id": guild_id, "idle_timeout_s": timeout},
                    )
                    await self._disconnect(guild_id, state)
            except asyncio.CancelledError:
                return

        state.idle_task = self.bot.loop.create_task(disconnect_when_idle())

    async def _cleanup_idle(self, state: GuildState) -> None:
        if state.now_playing is None and len(state.queue) == 0:
            await self._clear_now_playing_message(state)
            player = state.player
            guild = getattr(player, "guild", None) if player is not None else None
            guild_id = getattr(guild, "id", None)
            if guild_id is not None:
                self._schedule_idle_disconnect(guild_id, state)

    async def _ensure_playing(self, guild_id: int) -> None:
        state = self._get_state(guild_id)
        async with state.playback_lock:
            if state.now_playing is None:
                await self._begin_playback(guild_id)
            await self._cleanup_idle(state)

    async def _skip_current(
        self,
        guild_id: int,
        state: GuildState,
    ) -> tuple[bool, str]:
        if state.player is None or state.now_playing is None:
            return False, "Nothing is playing right now."
        mafic_lib = self._resolve_mafic()
        skipped_disconnected = False
        current = state.now_playing
        self._suppress_next_track_end(state, current.handle.track)
        try:
            await state.player.stop()
        except mafic_lib.PlayerNotConnected:
            skipped_disconnected = True
            self.logger.warning(
                "Skip requested while player was disconnected",
                extra={
                    "guild_id": guild_id,
                    **self._player_connection_context(state.player),
                },
            )
        except Exception as exc:
            self.logger.warning(
                "Skip failed while stopping player",
                extra={"guild_id": guild_id},
                exc_info=exc,
            )
            return False, "Could not skip the current track right now."
        state.now_playing = None
        state.playback_started_at = 0.0
        await self._ensure_playing(guild_id)
        if skipped_disconnected:
            return True, "Player was disconnected, advancing to the next track."
        return True, "Skipped the current track."

    async def _stop(self, guild_id: int) -> None:
        state = self._get_state(guild_id)
        state.queue.clear()
        current = state.now_playing
        if current is not None:
            self._suppress_next_track_end(state, current.handle.track)
        state.now_playing = None
        state.playback_started_at = 0.0
        if state.player:
            try:
                await state.player.stop()
            except Exception:
                pass
        await self._clear_now_playing_message(state)
        self._schedule_idle_disconnect(guild_id, state)

    @staticmethod
    def _cancel_pending_end(state: GuildState) -> None:
        task = state.pending_end_task
        state.pending_end_task = None
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()

    async def _finalize_track_end(
        self,
        guild_id: int,
        entry: QueuedTrack,
        reason: str,
    ) -> None:
        state = self._states.get(guild_id)
        if state is None:
            return
        current = state.now_playing
        if current is None or current.id != entry.id:
            return
        state.pending_end_task = None
        state.now_playing = None
        state.playback_started_at = 0.0
        if reason == "FINISHED":
            if state.loop_mode == "track":
                state.queue.add_next(entry.clone())
            elif state.loop_mode == "queue":
                state.queue.add(entry.clone())
            elif state.autoplay and len(state.queue) == 0:
                await self._enqueue_autoplay_track(guild_id, state, entry)
        await self._ensure_playing(guild_id)

    def _schedule_track_end_grace(
        self,
        guild_id: int,
        state: GuildState,
        entry: QueuedTrack,
        reason: str,
    ) -> None:
        self._cancel_pending_end(state)
        delay = self._env_float("ELBOT_TRACK_END_GRACE", 0.75, minimum=0.1)

        async def finish_after_grace() -> None:
            try:
                await asyncio.sleep(delay)
                await self._finalize_track_end(guild_id, entry, reason)
            except asyncio.CancelledError:
                return

        state.pending_end_task = self.bot.loop.create_task(finish_after_grace())

    # ------------------------------------------------------------------
    # Slash commands
    # ------------------------------------------------------------------
    @nextcord.slash_command(name="play", description="Play a track from YouTube, Spotify, or a URL")
    async def play(
        self,
        interaction: nextcord.Interaction,
        query: str = nextcord.SlashOption(
            description="Song name, YouTube/Spotify link, or playlist URL.",
            autocomplete=True,
        ),
        play_next: bool = nextcord.SlashOption(
            description="Queue the track to play immediately after the current one.",
            default=False,
        ),
    ) -> None:
        # CRITICAL: Defer IMMEDIATELY to prevent timeout on slow systems like Raspberry Pi
        try:
            await interaction.response.defer(ephemeral=False)
        except Exception as e:
            self.logger.error("Failed to defer interaction: %s", e)
            return

        self.logger.info(
            "Slash play invoked",
            extra={
                "guild_id": getattr(interaction.guild, "id", None),
                "user_id": getattr(interaction.user, "id", None),
            },
        )
        player, error = await self._ensure_voice(interaction)
        if error:
            await safe_reply(
                interaction,
                embed=self.embed_factory.failure(error),
                ephemeral=True,
            )
            return
        assert interaction.guild is not None
        state = self._get_state(interaction.guild.id)
        async with state.lock:
            if play_next and state.now_playing and state.player:
                position = getattr(state.player, "position", 0)
                eta_ms = max(state.now_playing.handle.duration - int(position), 0)
            else:
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
                self.logger.error(
                    "Track load failure: %s",
                    self._safe_log_value(str(exc), limit=1200),
                )
                if getattr(exc, "cause", None):
                    self.logger.error(
                        "Underlying cause: %s",
                        self._safe_log_value(str(exc.cause), limit=1200),
                    )
                await safe_reply(
                    interaction,
                    embed=self.embed_factory.failure(str(exc)),
                    ephemeral=True,
                )
                return
            if play_next:
                state.queue.add_next(queued_track)
                queue_position = 1
            else:
                state.queue.add(queued_track)
                queue_position = len(state.queue)
            msg = await safe_reply(
                interaction,
                embed=self.embed_factory.queued(
                    queued_track,
                    position=queue_position,
                    eta_ms=eta_ms,
                ),
            )
            try:
                queued_track.queued_message_id = msg.id
            except Exception:
                queued_track.queued_message_id = None
            await self._ensure_playing(interaction.guild.id)
            await self._refresh_now_playing(interaction.guild.id)

    @play.on_autocomplete("query")
    async def play_autocomplete(
        self, interaction: nextcord.Interaction, value: str
    ) -> list:
        """Provide track suggestions for the `query` option.

        Tries Lavalink search first, then falls back to yt-dlp search
        if Lavalink returns no results (e.g. YouTube blocking).
        Discord requires a response within ~3s, so the whole handler
        runs under a strict time budget; failures are swallowed so
        autocomplete remains responsive.
        """
        if not value:
            return []

        cache_key = " ".join(value.lower().split())
        cached = self._autocomplete_cache.get(cache_key)
        if cached is not None:
            return cached

        budget = self._env_float("ELBOT_AUTOCOMPLETE_BUDGET", 2.7, minimum=1.0)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + budget

        tracks = []
        # Try Lavalink search first, but cap it so the yt-dlp fallback
        # still has time to run within the budget.
        lavalink_timeout = min(1.5, budget)

        async def _lavalink_search() -> list:
            await self.backend.wait_ready(timeout=lavalink_timeout)
            return await self.backend.resolve_tracks(value, prefer_search=True)

        try:
            tracks = await asyncio.wait_for(
                _lavalink_search(), timeout=lavalink_timeout
            )
        except Exception as exc:
            self.logger.debug("Autocomplete Lavalink search failed: %s", exc)

        # yt-dlp searches run in worker threads that cannot be cancelled once
        # Discord's autocomplete deadline expires. Keep this opt-in on small
        # hosts such as the Raspberry Pi to avoid accumulating extraction work
        # while a user types.
        ytdlp_autocomplete = os.getenv("ELBOT_AUTOCOMPLETE_YTDLP", "0") == "1"
        if not tracks and ytdlp_autocomplete:
            remaining = deadline - loop.time()
            if remaining < 0.3:
                return []
            try:
                tracks = await self._ytdlp_search(value, timeout=remaining)
            except Exception as exc:
                self.logger.warning("Autocomplete yt-dlp search failed: %s", exc)
                return []
        if not tracks:
            return []

        choices: Dict[str, str] = {}
        for t in tracks[:7]:
            title = getattr(t, "title", None) or ""
            dur_ms = int(getattr(t, "duration", 0) or 0)
            mm, ss = divmod(dur_ms // 1000, 60)
            label = (
                f"{title} - {mm:02d}:{ss:02d}"
                if title
                else f"{value}"
            )
            val = getattr(t, "uri", None) or title or value
            choices[label[:100]] = str(val)[:100]
        if choices:
            self._autocomplete_cache[cache_key] = choices
        return choices

    async def _ytdlp_search(
        self, query: str, count: int = 7, timeout: float = 2.5
    ) -> list:
        """Search YouTube via yt-dlp and return lightweight result objects."""
        from types import SimpleNamespace

        import yt_dlp

        options = self.cookies.yt_dlp_options()
        options.update({
            "skip_download": True,
            "extract_flat": True,
            "quiet": True,
            "no_warnings": True,
        })

        search_query = f"ytsearch{count}:{query}"

        def _do_search() -> list:
            with yt_dlp.YoutubeDL(options) as ydl:
                result = ydl.extract_info(search_query, download=False)
                entries = result.get("entries", []) if result else []
                items = []
                for e in entries:
                    if not e:
                        continue
                    vid_id = e.get("id", "")
                    uri = e.get("url") or (
                        f"https://www.youtube.com/watch?v={vid_id}"
                        if vid_id else ""
                    )
                    items.append(SimpleNamespace(
                        title=e.get("title", ""),
                        # yt-dlp reports seconds; normalize to ms like Lavalink.
                        duration=int(e.get("duration") or 0) * 1000,
                        uri=uri,
                    ))
                return items

        return await asyncio.wait_for(
            asyncio.to_thread(_do_search),
            timeout=timeout,
        )

    @nextcord.slash_command(name="skip", description="Skip the current track")
    async def skip(self, interaction: nextcord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if guild is None:
            await safe_reply(
                interaction,
                "This command can only be used in guilds.",
                ephemeral=True,
            )
            return
        state = self._get_state(guild.id)
        if not state.player or not state.now_playing:
            await safe_reply(
                interaction,
                "Nothing is playing right now.",
                ephemeral=True,
            )
            return
        control_error = self._control_error(interaction, state)
        if control_error:
            await safe_reply(interaction, control_error, ephemeral=True)
            return
        success, message = await self._skip_current(guild.id, state)
        await safe_reply(interaction, message, ephemeral=not success)

    @nextcord.slash_command(name="pause", description="Pause the current track")
    async def pause(self, interaction: nextcord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if guild is None:
            await safe_reply(interaction, "This command can only be used in guilds.", ephemeral=True)
            return
        state = self._get_state(guild.id)
        if not state.player or not state.now_playing:
            await safe_reply(interaction, "Nothing is playing right now.", ephemeral=True)
            return
        control_error = self._control_error(interaction, state)
        if control_error:
            await safe_reply(interaction, control_error, ephemeral=True)
            return
        if getattr(state.player, "paused", False):
            await safe_reply(interaction, "Playback is already paused.", ephemeral=True)
            return
        await state.player.pause()
        await self._refresh_now_playing(guild.id)
        await safe_reply(interaction, "Playback paused.")

    @nextcord.slash_command(name="resume", description="Resume the paused track")
    async def resume(self, interaction: nextcord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if guild is None:
            await safe_reply(interaction, "This command can only be used in guilds.", ephemeral=True)
            return
        state = self._get_state(guild.id)
        if not state.player or not state.now_playing:
            await safe_reply(interaction, "Nothing is playing right now.", ephemeral=True)
            return
        control_error = self._control_error(interaction, state)
        if control_error:
            await safe_reply(interaction, control_error, ephemeral=True)
            return
        if not getattr(state.player, "paused", False):
            await safe_reply(interaction, "Playback is not paused.", ephemeral=True)
            return
        await state.player.resume()
        await self._refresh_now_playing(guild.id)
        await safe_reply(interaction, "Playback resumed.")

    @nextcord.slash_command(name="volume", description="Set playback volume")
    async def volume(
        self,
        interaction: nextcord.Interaction,
        level: int = nextcord.SlashOption(
            description="Volume from 0 to 200 percent.", min_value=0, max_value=200
        ),
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if guild is None:
            await safe_reply(interaction, "This command can only be used in guilds.", ephemeral=True)
            return
        state = self._get_state(guild.id)
        if not state.player:
            await safe_reply(interaction, "I'm not connected to voice.", ephemeral=True)
            return
        control_error = self._control_error(interaction, state)
        if control_error:
            await safe_reply(interaction, control_error, ephemeral=True)
            return
        state.volume = level
        await state.player.set_volume(level)
        await self._refresh_now_playing(guild.id)
        await safe_reply(interaction, f"Volume set to **{level}%**.")

    @nextcord.slash_command(name="seek", description="Seek within the current track")
    async def seek(
        self,
        interaction: nextcord.Interaction,
        position: str = nextcord.SlashOption(
            description="Position in seconds, mm:ss, or hh:mm:ss."
        ),
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if guild is None:
            await safe_reply(interaction, "This command can only be used in guilds.", ephemeral=True)
            return
        state = self._get_state(guild.id)
        if not state.player or not state.now_playing:
            await safe_reply(interaction, "Nothing is playing right now.", ephemeral=True)
            return
        control_error = self._control_error(interaction, state)
        if control_error:
            await safe_reply(interaction, control_error, ephemeral=True)
            return
        position_ms = self._parse_seek_position(position)
        if position_ms is None:
            await safe_reply(interaction, "Use seconds, `mm:ss`, or `hh:mm:ss`.", ephemeral=True)
            return
        duration = state.now_playing.handle.duration
        if duration > 0 and position_ms >= duration:
            await safe_reply(interaction, "That position is past the end of the track.", ephemeral=True)
            return
        await state.player.seek(position_ms)
        await self._refresh_now_playing(guild.id)
        await safe_reply(interaction, f"Seeked to **{position}**.")

    @nextcord.slash_command(name="nowplaying", description="Show the current track")
    async def nowplaying(self, interaction: nextcord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if guild is None:
            await safe_reply(interaction, "This command can only be used in guilds.", ephemeral=True)
            return
        state = self._get_state(guild.id)
        if not state.now_playing:
            await safe_reply(interaction, "Nothing is playing right now.", ephemeral=True)
            return
        embed = self._now_playing_embed(guild.id)
        await safe_reply(interaction, embed=embed)

    @nextcord.slash_command(name="loop", description="Set the repeat mode")
    async def set_loop(
        self,
        interaction: nextcord.Interaction,
        mode: str = nextcord.SlashOption(
            description="Choose what should repeat.",
            choices={"Off": "off", "Current track": "track", "Whole queue": "queue"},
        ),
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if guild is None:
            await safe_reply(interaction, "This command can only be used in guilds.", ephemeral=True)
            return
        state = self._get_state(guild.id)
        control_error = self._control_error(interaction, state)
        if control_error:
            await safe_reply(interaction, control_error, ephemeral=True)
            return
        state.loop_mode = mode
        labels = {"off": "off", "track": "current track", "queue": "whole queue"}
        await self._refresh_now_playing(guild.id)
        await safe_reply(interaction, f"Repeat mode set to **{labels[mode]}**.")

    @nextcord.slash_command(
        name="autoplay",
        description="Keep playing related tracks when the queue ends",
    )
    async def set_autoplay(
        self,
        interaction: nextcord.Interaction,
        enabled: bool = nextcord.SlashOption(
            description="Turn automatic related tracks on or off."
        ),
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if guild is None:
            await safe_reply(
                interaction,
                "This command can only be used in guilds.",
                ephemeral=True,
            )
            return
        state = self._get_state(guild.id)
        control_error = self._control_error(interaction, state)
        if control_error:
            await safe_reply(interaction, control_error, ephemeral=True)
            return
        state.autoplay = enabled
        await self._refresh_now_playing(guild.id)
        await safe_reply(
            interaction,
            f"AutoPlay turned **{'on' if enabled else 'off'}**.",
        )

    @nextcord.slash_command(name="clear", description="Clear the queued tracks")
    async def clear(self, interaction: nextcord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if guild is None:
            await safe_reply(interaction, "This command can only be used in guilds.", ephemeral=True)
            return
        state = self._get_state(guild.id)
        control_error = self._control_error(interaction, state)
        if control_error:
            await safe_reply(interaction, control_error, ephemeral=True)
            return
        count = len(state.queue)
        state.queue.clear()
        await self._refresh_now_playing(guild.id)
        await safe_reply(interaction, f"Cleared **{count}** queued track(s).")

    @nextcord.slash_command(name="disconnect", description="Stop music and leave voice")
    async def disconnect(self, interaction: nextcord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if guild is None:
            await safe_reply(interaction, "This command can only be used in guilds.", ephemeral=True)
            return
        state = self._states.get(guild.id)
        if state is None or state.player is None:
            await safe_reply(interaction, "I'm not connected to voice.", ephemeral=True)
            return
        control_error = self._control_error(interaction, state)
        if control_error:
            await safe_reply(interaction, control_error, ephemeral=True)
            return
        await self._disconnect(guild.id, state)
        await safe_reply(interaction, "Disconnected and cleared the queue.")

    @nextcord.slash_command(
        name="stop", description="Stop playback and clear the queue"
    )
    async def stop(self, interaction: nextcord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if guild is None:
            await safe_reply(
                interaction,
                "This command can only be used in guilds.",
                ephemeral=True,
            )
            return
        state = self._get_state(guild.id)
        control_error = self._control_error(interaction, state)
        if control_error:
            await safe_reply(interaction, control_error, ephemeral=True)
            return
        await self._stop(guild.id)
        await safe_reply(interaction, "Playback stopped and queue cleared.")

    @nextcord.slash_command(name="queue", description="Show the current queue")
    async def show_queue(self, interaction: nextcord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if guild is None:
            await safe_reply(
                interaction,
                "This command can only be used in guilds.",
                ephemeral=True,
            )
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
            await safe_reply(interaction, embed=embed)
            return
        paginator = QueuePaginator(
            self.embed_factory,
            tracks,
            per_page=8,
            now_playing=state.now_playing,
        )
        await paginator.send_initial(interaction)

    @nextcord.slash_command(name="remove", description="Remove a queued track")
    async def remove(self, interaction: nextcord.Interaction, target: str) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if guild is None:
            await safe_reply(
                interaction,
                "This command can only be used in guilds.",
                ephemeral=True,
            )
            return
        state = self._get_state(guild.id)
        control_error = self._control_error(interaction, state)
        if control_error:
            await safe_reply(interaction, control_error, ephemeral=True)
            return
        removed: Optional[QueuedTrack] = None
        removed_many = []
        if "-" in target:
            try:
                start_str, end_str = target.split("-", 1)
                start = int(start_str.strip()) - 1
                end = int(end_str.strip()) - 1
            except ValueError:
                await safe_reply(interaction, "Invalid range.", ephemeral=True)
                return
            removed_many = state.queue.remove_range(start, end)
        else:
            try:
                index = int(target) - 1
            except ValueError:
                await safe_reply(interaction, "Invalid index.", ephemeral=True)
                return
            removed = state.queue.remove_index(index)
        if removed_many:
            await safe_reply(
                interaction,
                f"Removed {len(removed_many)} tracks from the queue.",
            )
        elif removed:
            await safe_reply(
                interaction,
                f"Removed **{removed.handle.title}** from the queue.",
            )
        else:
            await safe_reply(interaction, "No tracks removed.", ephemeral=True)

    @nextcord.slash_command(
        name="move", description="Move a track to a different position"
    )
    async def move(
        self, interaction: nextcord.Interaction, source: int, destination: int
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if guild is None:
            await safe_reply(
                interaction,
                "This command can only be used in guilds.",
                ephemeral=True,
            )
            return
        state = self._get_state(guild.id)
        control_error = self._control_error(interaction, state)
        if control_error:
            await safe_reply(interaction, control_error, ephemeral=True)
            return
        success = state.queue.move(source - 1, destination - 1)
        if success:
            await safe_reply(interaction, "Track moved.")
        else:
            await safe_reply(interaction, "Invalid indices provided.", ephemeral=True)

    @nextcord.slash_command(name="shuffle", description="Shuffle the queue")
    async def shuffle(self, interaction: nextcord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if guild is None:
            await safe_reply(
                interaction,
                "This command can only be used in guilds.",
                ephemeral=True,
            )
            return
        state = self._get_state(guild.id)
        control_error = self._control_error(interaction, state)
        if control_error:
            await safe_reply(interaction, control_error, ephemeral=True)
            return
        state.queue.shuffle()
        await safe_reply(interaction, "Queue shuffled.")

    @nextcord.slash_command(name="replay", description="Replay the last played track")
    async def replay(self, interaction: nextcord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if guild is None:
            await safe_reply(
                interaction,
                "This command can only be used in guilds.",
                ephemeral=True,
            )
            return
        state = self._get_state(guild.id)
        control_error = self._control_error(interaction, state)
        if control_error:
            await safe_reply(interaction, control_error, ephemeral=True)
            return
        replayed = state.queue.replay_last()
        if not replayed:
            await safe_reply(interaction, "Nothing to replay.", ephemeral=True)
            return
        await safe_reply(
            interaction,
            embed=self.embed_factory.queued(
                replayed,
                position=1,
                eta_ms=self._calculate_eta_ms(guild.id),
            ),
        )
        await self._ensure_playing(guild.id)

    @nextcord.slash_command(
        name="ytcheck", description="Show YouTube stack diagnostics"
    )
    async def ytcheck(self, interaction: nextcord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            report = await self.diagnostics.collect()
        except Exception as exc:  # pragma: no cover - diagnostics failure
            await safe_reply(
                interaction,
                f"Diagnostics failed: {exc}",
                ephemeral=True,
            )
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
        embed = nextcord.Embed(
            title="YouTube diagnostics", description="\n".join(fields)
        )
        await safe_reply(interaction, embed=embed)

    # ------------------------------------------------------------------
    # Mafic event listeners
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_track_end(
        self, event: mafic.TrackEndEvent
    ) -> None:  # pragma: no cover - integration
        guild_id = event.player.guild.id
        state = self._states.get(guild_id)
        if not state:
            return
        event_track = getattr(event, "track", None)
        event_key = self._track_key(event_track)
        if event_key and event_key in state.suppressed_track_keys:
            state.suppressed_track_keys.discard(event_key)
            self.logger.info(
                "Ignoring expected track-end event after a control action",
                extra={"guild_id": guild_id},
            )
            return
        current_entry = state.now_playing
        if current_entry is None:
            return
        current_key = self._track_key(current_entry.handle.track)
        if event_key and current_key and event_key != current_key:
            self.logger.info(
                "Ignoring stale track-end event",
                extra={"guild_id": guild_id},
            )
            return
        track_obj = event_track or getattr(event.player, "current", None)
        context = self._track_log_context(guild_id, current_entry, track_obj)
        reason = self._normalise_end_reason(event.reason)
        context["end_reason"] = reason
        title = context.get("track_title") or "unknown track"
        # If on_track_exception is resolving a fallback, don't advance the
        # queue — the exception handler will do it once the fallback is ready.
        if state._fallback_pending:
            self.logger.info(
                "Track end ignored (fallback pending): %s", title, extra=context,
            )
            return
        now = time.monotonic()
        state.last_ended = current_entry
        state.last_ended_at = now
        position = int(getattr(event.player, "position", 0) or 0)
        elapsed_ms = int(max(0.0, now - state.playback_started_at) * 1000)
        duration = current_entry.handle.duration
        progress = max(position, elapsed_ms)
        ended_early = duration > 10_000 and progress + 5_000 < duration
        context["position_ms"] = position
        context["elapsed_ms"] = elapsed_ms
        if reason != "FINISHED" or ended_early:
            self.logger.warning(
                "Track ended early (%s): %s",
                reason,
                title,
                extra=context,
            )
        else:
            self.logger.info(
                "Track finished: %s",
                title,
                extra=context,
            )
        if ended_early:
            # youtube-source can emit FINISHED just before TrackExceptionEvent.
            # Keep the current entry briefly so the exception can replace it
            # without advancing and then clobbering the next queued track.
            self._schedule_track_end_grace(
                guild_id, state, current_entry, reason
            )
            return
        await self._finalize_track_end(guild_id, current_entry, reason)

    @commands.Cog.listener()
    async def on_track_exception(
        self, event: mafic.TrackExceptionEvent
    ) -> None:  # pragma: no cover
        guild_id = event.player.guild.id
        state = self._states.get(guild_id)
        if not state:
            return
        event_track = getattr(event, "track", None)
        event_key = self._track_key(event_track)
        current_entry = state.now_playing
        if current_entry is not None:
            current_key = self._track_key(current_entry.handle.track)
            if event_key and current_key and event_key != current_key:
                self.logger.info(
                    "Ignoring stale track-exception event",
                    extra={"guild_id": guild_id},
                )
                return
        if current_entry is None and state.last_ended is not None:
            # track_end for this failure arrived first and already cleared
            # now_playing; recover the entry so the fallback still runs.
            if time.monotonic() - state.last_ended_at < 5.0:
                current_entry = state.last_ended
                self.logger.info(
                    "Track exception after track_end; recovering just-ended entry",
                    extra={
                        "guild_id": guild_id,
                        "track_query": self._safe_log_value(current_entry.query),
                    },
                )
            state.last_ended = None
        if current_entry is None:
            return
        self._cancel_pending_end(state)
        track_obj = event_track or getattr(event.player, "current", None)
        context = self._track_log_context(guild_id, current_entry, track_obj)
        exception = event.exception
        raw_message = getattr(exception, "message", None) or str(exception)
        message = str(self._safe_log_value(raw_message, limit=1200))
        severity = getattr(exception, "severity", None) or "unknown"
        cause = getattr(exception, "cause", None)
        context["exception_message"] = message
        context["exception_severity"] = getattr(exception, "severity", None)
        if cause is not None:
            context["exception_cause"] = self._safe_log_value(
                str(cause), limit=1200
            )
        self.metrics.incr_failed()
        self.logger.error("Track exception [%s]: %s", severity, message, extra=context)

        if current_entry and not current_entry.is_fallback:
            # Signal on_track_end to not advance the queue while we resolve.
            state._fallback_pending = True
            base_error = TrackLoadFailure(
                message, cause=exception if isinstance(exception, Exception) else None
            )
            # If the original query is a non-YouTube URL (e.g. Spotify),
            # yt-dlp won't know how to handle it. Use the resolved
            # title + author as a search query instead.
            fallback_query = current_entry.query
            if fallback_query.startswith("http") and "youtube.com" not in fallback_query and "youtu.be" not in fallback_query:
                title = getattr(current_entry.handle, "title", "")
                author = getattr(current_entry.handle, "author", "")
                if title:
                    fallback_query = f"{title} {author}".strip()
                    self.logger.info(
                        "Rewrote non-YouTube URL to search query for fallback",
                        extra={
                            "original": self._safe_log_value(current_entry.query),
                            "rewritten": self._safe_log_value(fallback_query),
                        },
                    )
            fallback_entry = None
            try:
                fallback_entry = await self.fallback.build_fallback_entry(
                    fallback_query,
                    requested_by=current_entry.requested_by,
                    requester_display=current_entry.requester_display,
                    channel_id=current_entry.channel_id,
                    base_error=base_error,
                )
            except Exception as fallback_exc:
                context["fallback_error"] = self._safe_log_value(
                    str(fallback_exc), limit=1200
                )
                self.logger.error(
                    "Fallback resolution failed",
                    extra=context,
                    exc_info=not isinstance(fallback_exc, TrackLoadFailure),
                )
            finally:
                # Always clear the flag: leaving it set would make
                # on_track_end ignore every future event for this guild.
                state._fallback_pending = False
            if state.now_playing and state.now_playing.id == current_entry.id:
                state.now_playing = None
                state.playback_started_at = 0.0
            if fallback_entry is not None:
                context_fallback = self._track_log_context(guild_id, fallback_entry)
                context_fallback["fallback_trigger"] = "track_exception"
                self.logger.info("Switching to fallback stream", extra=context_fallback)
                state.queue.add_next(fallback_entry)
            await self._ensure_playing(guild_id)
            return

        if state.now_playing and state.now_playing.id == current_entry.id:
            state.now_playing = None
            state.playback_started_at = 0.0
        await self._ensure_playing(guild_id)

    @commands.Cog.listener()
    async def on_track_stuck(
        self, event: mafic.TrackStuckEvent
    ) -> None:  # pragma: no cover
        guild_id = event.player.guild.id
        state = self._states.get(guild_id)
        if not state:
            return
        current_entry = state.now_playing
        if current_entry is None:
            return
        event_track = getattr(event, "track", None)
        event_key = self._track_key(event_track)
        current_key = self._track_key(current_entry.handle.track)
        if event_key and current_key and event_key != current_key:
            self.logger.info(
                "Ignoring stale track-stuck event",
                extra={"guild_id": guild_id},
            )
            return
        self._cancel_pending_end(state)
        track_obj = event_track or getattr(event.player, "current", None)
        context = self._track_log_context(guild_id, current_entry, track_obj)
        threshold = getattr(event, "threshold", None)
        context["threshold_ms"] = threshold
        title = context.get("track_title") or "unknown track"
        self.metrics.incr_failed()
        self.logger.warning("Track stuck at %s ms: %s", threshold, title, extra=context)

        if current_entry and not current_entry.is_fallback:
            # Same race guard as on_track_exception: a stuck track may still
            # emit track_end while the fallback is being resolved.
            state._fallback_pending = True
            base_error = TrackLoadFailure(
                f"Track stuck after {threshold} ms", cause=None
            )
            fallback_entry = None
            try:
                fallback_entry = await self.fallback.build_fallback_entry(
                    current_entry.query,
                    requested_by=current_entry.requested_by,
                    requester_display=current_entry.requester_display,
                    channel_id=current_entry.channel_id,
                    base_error=base_error,
                )
            except Exception as fallback_exc:
                context["fallback_error"] = self._safe_log_value(
                    str(fallback_exc), limit=1200
                )
                self.logger.error(
                    "Fallback resolution failed after track stuck",
                    extra=context,
                    exc_info=not isinstance(fallback_exc, TrackLoadFailure),
                )
            finally:
                state._fallback_pending = False
            if state.now_playing and state.now_playing.id == current_entry.id:
                state.now_playing = None
                state.playback_started_at = 0.0
            if fallback_entry is not None:
                context_fallback = self._track_log_context(guild_id, fallback_entry)
                context_fallback["fallback_trigger"] = "track_stuck"
                self.logger.info("Switching to fallback stream", extra=context_fallback)
                state.queue.add_next(fallback_entry)
            await self._ensure_playing(guild_id)
            return

        if state.now_playing and state.now_playing.id == current_entry.id:
            state.now_playing = None
            state.playback_started_at = 0.0
        await self._ensure_playing(guild_id)


def setup(bot: commands.Bot) -> None:
    bot.add_cog(Music(bot))

