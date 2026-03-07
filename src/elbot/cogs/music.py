"""Nextcord music cog backed by Lavalink v4 with yt-dlp fallback."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Dict, Optional, TYPE_CHECKING

import nextcord

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
    SearchCache,
    FallbackPlayer,
    LavalinkAudioBackend,
    MusicQueue,
    PlaybackMetrics,
    QueuedTrack,
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
    now_playing: Optional[QueuedTrack] = None
    player: Optional[object] = None
    last_channel_id: Optional[int] = None
    now_playing_message: Optional[nextcord.Message] = None


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
        if state.player:
            try:
                await state.player.disconnect(force=True)
            except Exception:  # pragma: no cover - defensive cleanup
                pass
        await self._clear_now_playing_message(state)
        self._states.pop(guild_id, None)

    async def _ensure_voice(
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
                await voice.disconnect(force=True)
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
                voice = await target_channel.connect(
                    cls=mafic_lib.Player,
                    timeout=connect_timeout,
                    reconnect=True,
                )
            except Exception as exc:
                self.logger.error(
                    "Voice connection failed",
                    extra={"guild_id": guild.id, "voice_channel_id": target_channel.id},
                    exc_info=exc,
                )
                return None, "Could not join your voice channel."

        state.player = voice
        state.last_channel_id = interaction.channel_id
        if not await self._wait_for_player_connection(voice, connect_timeout):
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
                await voice.disconnect(force=True)
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
                context["fallback_source"] = entry.fallback_source
            context["track_query"] = entry.query

        if handle is not None:
            context.update(
                {
                    "track_title": handle.title,
                    "track_author": handle.author,
                    "track_source": handle.source,
                    "track_duration": handle.duration,
                    "track_uri": handle.uri,
                }
            )
            try:
                context.setdefault(
                    "track_identifier", getattr(handle.track, "identifier", None)
                )
                context.setdefault("track_id", getattr(handle.track, "id", None))
            except AttributeError:
                pass

        if track is not None:
            context.setdefault("track_title", getattr(track, "title", None))
            context.setdefault("track_author", getattr(track, "author", None))
            context.setdefault("track_source", getattr(track, "source", None))
            context.setdefault("track_duration", getattr(track, "length", None))
            context.setdefault("track_uri", getattr(track, "uri", None))
            context.setdefault("track_identifier", getattr(track, "identifier", None))
            context.setdefault("track_id", getattr(track, "id", None))

        return context

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
        channel = getattr(player, "channel", None)
        if channel is None:
            self.logger.warning(
                "Reconnect skipped: player has no bound channel",
                extra={"guild_id": guild_id},
            )
            return None
        try:
            await player.disconnect(force=True)
        except Exception:
            pass
        try:
            new_player = await channel.connect(
                cls=mafic_lib.Player,
                timeout=connect_timeout,
                reconnect=True,
            )
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
                return
            if latest_player is not player:
                player = latest_player
            try:
                await player.play(next_track.handle.track)
                state.player = player
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
                await failed_player.disconnect(force=True)
            except Exception:
                pass
            state.player = None
        state.queue.add_next(next_track)
        state.now_playing = None
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
                    embed = self.embed_factory.now_playing(track, position=0, eta_ms=0)
                    await queued_msg.edit(embed=embed)
                    state.now_playing_message = queued_msg
                    return
                except Exception:
                    # fetching/editing failed; fall back to sending a new message
                    pass
            await self._clear_now_playing_message(state)
            embed = self.embed_factory.now_playing(track, position=0, eta_ms=0)
            message = await channel.send(embed=embed)
            state.now_playing_message = message

    async def _clear_now_playing_message(self, state: GuildState) -> None:
        message = state.now_playing_message
        if not message:
            return
        state.now_playing_message = None
        try:
            await message.delete()
        except Exception:
            pass

    async def _cleanup_idle(self, state: GuildState) -> None:
        if state.now_playing is None and len(state.queue) == 0:
            await self._clear_now_playing_message(state)

    async def _ensure_playing(self, guild_id: int) -> None:
        state = self._get_state(guild_id)
        async with state.playback_lock:
            if state.now_playing is None:
                await self._begin_playback(guild_id)
            await self._cleanup_idle(state)

    async def _stop(self, guild_id: int) -> None:
        state = self._get_state(guild_id)
        state.queue.clear()
        state.now_playing = None
        if state.player:
            try:
                await state.player.stop()
            except Exception:
                pass
        await self._clear_now_playing_message(state)

    # ------------------------------------------------------------------
    # Slash commands
    # ------------------------------------------------------------------
    @nextcord.slash_command(name="play", description="Play a YouTube track")
    async def play(
        self,
        interaction: nextcord.Interaction,
        query: str = nextcord.SlashOption(
            description="Type music name, link, playlist, radio and media link.",
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
                self.logger.error("Track load failure", exc_info=exc)
                if getattr(exc, "cause", None):
                    self.logger.error("Underlying cause: %s", exc.cause)
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

    @play.on_autocomplete("query")
    async def play_autocomplete(
        self, interaction: nextcord.Interaction, value: str
    ) -> list:
        """Provide track suggestions for the `query` option.

        Uses the Lavalink resolver to fetch search results and returns a
        compact list of choices containing title and duration. Failures
        are swallowed so autocomplete remains responsive.
        """
        try:
            if not value:
                return []
            # Try to resolve via the Lavalink backend. If the node is not
            # ready or resolution fails, return an empty list instead of
            # raising so the autocomplete UI stays responsive.
            try:
                # Ensure backend is available (this will initialize lazily)
                await self.backend.wait_ready()
                tracks = await self.backend.resolve_tracks(value, prefer_search=True)
            except Exception:
                return []

            choices = []
            for t in tracks[:7]:
                dur = int(getattr(t, "duration", 0) or 0)
                mm = dur // 60
                ss = dur % 60
                label = (
                    f"{t.title} - {mm:02d}:{ss:02d}"
                    if getattr(t, "title", None)
                    else f"{value}"
                )
                val = t.uri or t.title or value
                try:
                    choices.append(
                        nextcord.SlashOptionChoice(name=label[:100], value=str(val))
                    )
                except Exception:
                    # If the choice object fails for any reason, skip it.
                    continue
            return choices
        except Exception:
            return []

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
        mafic_lib = self._resolve_mafic()
        skipped_disconnected = False
        try:
            await state.player.stop()
        except mafic_lib.PlayerNotConnected:
            skipped_disconnected = True
            self.logger.warning(
                "Skip requested while player was disconnected",
                extra={
                    "guild_id": guild.id,
                    **self._player_connection_context(state.player),
                },
            )
        except Exception as exc:
            self.logger.warning(
                "Skip failed while stopping player",
                extra={"guild_id": guild.id},
                exc_info=exc,
            )
            await safe_reply(
                interaction,
                "Could not skip the current track right now.",
                ephemeral=True,
            )
            return
        state.now_playing = None
        if skipped_disconnected:
            await safe_reply(
                interaction,
                "Player was disconnected, advancing to the next track.",
                ephemeral=True,
            )
        else:
            await safe_reply(interaction, "Skipped the current track.")
        await self._ensure_playing(guild.id)

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
        current_entry = state.now_playing
        track_obj = getattr(event, "track", None) or getattr(
            event.player, "current", None
        )
        context = self._track_log_context(guild_id, current_entry, track_obj)
        reason = event.reason or "UNKNOWN"
        context["end_reason"] = reason
        title = context.get("track_title") or "unknown track"
        state.now_playing = None
        if reason != "FINISHED":
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
        await self._ensure_playing(guild_id)

    @commands.Cog.listener()
    async def on_track_exception(
        self, event: mafic.TrackExceptionEvent
    ) -> None:  # pragma: no cover
        guild_id = event.player.guild.id
        state = self._states.get(guild_id)
        if not state:
            return
        current_entry = state.now_playing
        track_obj = getattr(event, "track", None) or getattr(
            event.player, "current", None
        )
        context = self._track_log_context(guild_id, current_entry, track_obj)
        exception = event.exception
        message = getattr(exception, "message", None) or str(exception)
        severity = getattr(exception, "severity", None) or "unknown"
        cause = getattr(exception, "cause", None)
        context["exception_message"] = message
        context["exception_severity"] = getattr(exception, "severity", None)
        if cause is not None:
            context["exception_cause"] = str(cause)
        self.metrics.incr_failed()
        self.logger.error("Track exception [%s]: %s", severity, message, extra=context)

        if current_entry and not current_entry.is_fallback:
            base_error = TrackLoadFailure(
                message, cause=exception if isinstance(exception, Exception) else None
            )
            try:
                fallback_entry = await self.fallback.build_fallback_entry(
                    current_entry.query,
                    requested_by=current_entry.requested_by,
                    requester_display=current_entry.requester_display,
                    channel_id=current_entry.channel_id,
                    base_error=base_error,
                )
            except TrackLoadFailure as fallback_exc:
                context["fallback_error"] = str(fallback_exc)
                self.logger.error("Fallback resolution failed", extra=context)
                state.now_playing = None
                await self._ensure_playing(guild_id)
                return
            else:
                context_fallback = self._track_log_context(guild_id, fallback_entry)
                context_fallback["fallback_trigger"] = "track_exception"
                self.logger.info("Switching to fallback stream", extra=context_fallback)
                state.now_playing = None
                state.queue.add_next(fallback_entry)
                await self._ensure_playing(guild_id)
                return

        state.now_playing = None
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
        track_obj = getattr(event, "track", None) or getattr(
            event.player, "current", None
        )
        context = self._track_log_context(guild_id, current_entry, track_obj)
        threshold = getattr(event, "threshold", None)
        context["threshold_ms"] = threshold
        title = context.get("track_title") or "unknown track"
        self.metrics.incr_failed()
        self.logger.warning("Track stuck at %s ms: %s", threshold, title, extra=context)

        if current_entry and not current_entry.is_fallback:
            base_error = TrackLoadFailure(
                f"Track stuck after {threshold} ms", cause=None
            )
            try:
                fallback_entry = await self.fallback.build_fallback_entry(
                    current_entry.query,
                    requested_by=current_entry.requested_by,
                    requester_display=current_entry.requester_display,
                    channel_id=current_entry.channel_id,
                    base_error=base_error,
                )
            except TrackLoadFailure as fallback_exc:
                context["fallback_error"] = str(fallback_exc)
                self.logger.error(
                    "Fallback resolution failed after track stuck", extra=context
                )
                state.now_playing = None
                await self._ensure_playing(guild_id)
                return
            else:
                context_fallback = self._track_log_context(guild_id, fallback_entry)
                context_fallback["fallback_trigger"] = "track_stuck"
                self.logger.info("Switching to fallback stream", extra=context_fallback)
                state.now_playing = None
                state.queue.add_next(fallback_entry)
                await self._ensure_playing(guild_id)
                return

        state.now_playing = None
        await self._ensure_playing(guild_id)


def setup(bot: commands.Bot) -> None:
    bot.add_cog(Music(bot))

