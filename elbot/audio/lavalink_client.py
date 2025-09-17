"""Helpers for managing Mafic/Lavalink playback with yt-dlp fallback."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from elbot.audio.ytdlp_helper import (
    ExtractionError,
    YTDLPConfig,
    YTDLPExtractor,
    YTDLPResult,
)
from elbot.config import Config

# Mafic refuses to import when multiple discord libraries are present unless
# this environment variable is set.  We prefer nextcord but fall back to
# whichever compatible library is installed first (nextcord is listed before
# discord.py so we will still bind to nextcord in mixed environments).
os.environ.setdefault("MAFIC_IGNORE_LIBRARY_CHECK", "1")

import mafic

logger = logging.getLogger("elbot.audio.lavalink")

TrackSource = Literal["lavalink", "yt-dlp"]


class MusicError(RuntimeError):
    """Base error for music playback issues."""


class NodeNotReadyError(MusicError):
    """Raised when the Lavalink node is unavailable."""


class NoResultsError(MusicError):
    """Raised when neither Lavalink nor yt-dlp could resolve a track."""


@dataclass(slots=True)
class LavalinkTrack:
    """Container for a Lavalink track enriched with metadata."""

    track: mafic.Track
    title: str
    uri: str | None
    requester_id: int
    source: TrackSource
    length: int | None
    artwork_url: str | None
    is_stream: bool

    @classmethod
    def from_mafic(
        cls,
        track: mafic.Track,
        *,
        requester_id: int,
        source: TrackSource = "lavalink",
    ) -> "LavalinkTrack":
        return cls(
            track=track,
            title=track.title,
            uri=track.uri,
            requester_id=requester_id,
            source=source,
            length=track.length,
            artwork_url=getattr(track, "artwork_url", None),
            is_stream=track.stream,
        )

    @classmethod
    def from_ytdlp(
        cls, track: mafic.Track, info: YTDLPResult, *, requester_id: int
    ) -> "LavalinkTrack":
        return cls(
            track=track,
            title=info.title,
            uri=info.webpage_url,
            requester_id=requester_id,
            source="yt-dlp",
            length=info.duration,
            artwork_url=info.thumbnail,
            is_stream=track.stream,
        )


class LavalinkManager:
    """Encapsulates Mafic's node pooling, retries and fallbacks."""

    __slots__ = (
        "bot",
        "node_label",
        "_pool",
        "_node",
        "_ready",
        "_connect_lock",
        "_connect_task",
        "_yt_dlp",
    )

    def __init__(self, bot) -> None:
        self.bot = bot
        self.node_label = "main"
        self._pool: mafic.NodePool = mafic.NodePool(bot)
        self._node: mafic.Node | None = None
        self._ready = asyncio.Event()
        self._connect_lock = asyncio.Lock()

        cookies_path = Config.YTDLP_COOKIES_FILE
        config = YTDLPConfig(Path(cookies_path).expanduser()) if cookies_path else None
        self._yt_dlp = YTDLPExtractor(config)

        self._connect_task: asyncio.Task[None] | None = self.bot.loop.create_task(
            self._connect_loop()
        )

    # ------------------------------------------------------------------
    # Connection handling
    # ------------------------------------------------------------------
    async def _connect_loop(self) -> None:
        await self.bot.wait_until_ready()

        attempt = 0
        while not self.bot.is_closed():
            attempt += 1
            try:
                async with self._connect_lock:
                    await self._connect_once()
                return
            except Exception as exc:  # pragma: no cover - defensive logging
                delay = min(2 ** attempt, 30)
                logger.warning(
                    "Failed to connect to Lavalink (attempt %s): %s", attempt, exc
                )
                await asyncio.sleep(delay)

    async def _connect_once(self) -> None:
        host_raw = os.getenv("LAVALINK_HOST", Config.LAVALINK_HOST)
        port_raw = os.getenv("LAVALINK_PORT", str(Config.LAVALINK_PORT))
        password = os.getenv("LAVALINK_PASSWORD", Config.LAVALINK_PASSWORD)

        secure = False
        host = host_raw
        if host_raw.startswith("https://"):
            secure = True
            host = host_raw.removeprefix("https://")
        elif host_raw.startswith("http://"):
            host = host_raw.removeprefix("http://")

        try:
            port = int(port_raw)
        except ValueError:
            raise RuntimeError(f"Invalid LAVALINK_PORT value: {port_raw!r}") from None

        # Close existing node if we are reconnecting.
        if self._node is not None:
            try:
                await self._node.close()
            finally:
                self._node = None
                self._ready.clear()

        logger.info(
            "Connecting to Lavalink node %s:%s (secure=%s)", host, port, secure
        )

        node = await self._pool.create_node(
            host=host,
            port=port,
            label=self.node_label,
            password=password,
            secure=secure,
            player_cls=mafic.Player,
        )

        self._node = node
        # We rely on the node_ready event to flip the ready flag once Lavalink
        # has completed the handshake.

    def handle_node_ready(self, node: mafic.Node) -> None:
        if node.label != self.node_label:
            return

        logger.info("Lavalink node %s is ready", node.label)
        self._node = node
        self._ready.set()

    def handle_node_unavailable(self, node: mafic.Node) -> None:
        if node.label != self.node_label:
            return

        logger.warning("Lavalink node %s became unavailable", node.label)
        self._ready.clear()
        self._node = None
        if self._connect_task is None or self._connect_task.done():
            self._connect_task = self.bot.loop.create_task(self._connect_loop())

    async def wait_ready(self, timeout: float | None = None) -> bool:
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def close(self) -> None:
        if self._connect_task and not self._connect_task.done():
            self._connect_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):  # pragma: no cover
                await self._connect_task

        if self._node is not None:
            await self._node.close()
            self._node = None
        self._ready.clear()

    # ------------------------------------------------------------------
    # Track resolution
    # ------------------------------------------------------------------
    async def resolve(self, query: str, *, requester_id: int) -> LavalinkTrack:
        if not await self.wait_ready(timeout=10):
            raise NodeNotReadyError("The music node is still starting. Please try again soon.")

        if self._node is None:
            raise NodeNotReadyError("The Lavalink node is not available right now.")

        try:
            track = await self._load_with_retry(query)
            if track:
                return LavalinkTrack.from_mafic(track, requester_id=requester_id)
        except mafic.TrackLoadException as exc:
            logger.warning("Lavalink failed to load %s: %s", query, exc)
        except Exception as exc:  # pragma: no cover - unexpected
            logger.warning("Unexpected Lavalink error for %s: %s", query, exc)

        logger.info("Falling back to yt-dlp for %s", query)
        try:
            info = await self._yt_dlp.extract(query)
        except ExtractionError as exc:
            raise NoResultsError(str(exc)) from exc
        fallback_track = await self._load_direct_stream(info)
        if fallback_track is None:
            raise NoResultsError("The track could not be played even with yt-dlp fallback.")
        return LavalinkTrack.from_ytdlp(
            fallback_track, info, requester_id=requester_id
        )

    async def _load_with_retry(self, query: str) -> mafic.Track | None:
        assert self._node is not None

        attempt = 0
        last_error: Exception | None = None
        while attempt < 4:
            attempt += 1
            try:
                return await self._fetch_first_track(query)
            except mafic.TrackLoadException as exc:
                last_error = exc
                if not self._should_retry(exc) or attempt >= 4:
                    raise
                delay = min(2 ** attempt, 10)
                logger.debug("Retrying Lavalink load after %.1fs (%s)", delay, exc)
                await asyncio.sleep(delay)
            except Exception as exc:  # pragma: no cover - unexpected errors
                last_error = exc
                break

        if last_error:
            raise last_error
        return None

    async def _fetch_first_track(self, query: str) -> mafic.Track | None:
        assert self._node is not None

        results = await self._node.fetch_tracks(query, search_type=mafic.SearchType.YOUTUBE.value)
        if results is None:
            return None
        if isinstance(results, mafic.Playlist):
            return results.tracks[0] if results.tracks else None
        if isinstance(results, list):
            return results[0] if results else None
        return None

    async def _load_direct_stream(self, info: YTDLPResult) -> mafic.Track | None:
        assert self._node is not None
        try:
            results = await self._node.fetch_tracks(info.stream_url)
        except mafic.TrackLoadException as exc:  # pragma: no cover - depends on Lavalink state
            logger.error("Lavalink refused yt-dlp stream %s: %s", info.stream_url, exc)
            raise NoResultsError("Lavalink rejected the direct stream returned by yt-dlp.")

        if results is None:
            return None
        if isinstance(results, list):
            return results[0] if results else None
        if isinstance(results, mafic.Playlist):
            return results.tracks[0] if results.tracks else None
        return None

    @staticmethod
    def _should_retry(exc: mafic.TrackLoadException) -> bool:
        payload = f"{getattr(exc, 'message', '')} {getattr(exc, 'cause', '')}".lower()
        return any(keyword in payload for keyword in ("429", "throttl", "rate limit", "quota"))

