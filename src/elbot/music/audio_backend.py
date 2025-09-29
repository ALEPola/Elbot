"""Lavalink audio backend using Mafic."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
import inspect
from typing import Iterable, List, Optional

from elbot.config import get_lavalink_connection_info

os.environ.setdefault("MAFIC_LIBRARY", "nextcord")
os.environ.setdefault("MAFIC_IGNORE_LIBRARY_CHECK", "1")

# mafic is an optional dependency. Import lazily when actually used so tests
# that don't need music functionality can run without it.
mafic = None

__all__ = [
    "TrackHandle",
    "LavalinkAudioBackend",
    "LavalinkUnavailable",
    "TrackLoadFailure",
]


def _default_logger() -> logging.Logger:
    return logging.getLogger("elbot.music.lavalink")


@dataclass(slots=True)
class TrackHandle:
    """Light-weight wrapper around a Lavalink track."""

    # type: ignore[name-defined]
    track: "mafic.Track"
    title: str
    author: str
    duration: int
    uri: Optional[str]
    source: str

    @classmethod
    def from_mafic(cls, track: mafic.Track) -> "TrackHandle":
        info = getattr(track, "info", None)
        info_dict = info or {}

        title = (
            getattr(track, "title", None) or info_dict.get("title") or "Unknown title"
        )
        author = (
            getattr(track, "author", None) or info_dict.get("author") or "Unknown creator"
        )
        duration = (
            getattr(track, "length", None)
            or info_dict.get("length")
            or info_dict.get("duration")
            or 0
        )
        uri = getattr(track, "uri", None) or info_dict.get("uri")
        source = (
            getattr(track, "source", None) or info_dict.get("sourceName") or "unknown"
        )

        return cls(
            track=track,
            title=str(title),
            author=str(author),
            duration=int(duration),
            uri=uri,
            source=str(source),
        )


class LavalinkUnavailable(RuntimeError):
    """Raised when the Lavalink node is not ready."""


class TrackLoadFailure(RuntimeError):
    """Raised when a track fails to load."""

    def __init__(self, message: str, *, cause: Optional[BaseException] = None):
        super().__init__(message)
        self.__cause__ = cause
        self.cause = cause

    @property
    def is_retryable(self) -> bool:
        if not self.cause:
            return False
        text = f"{type(self.cause).__name__}:{self.cause}".lower()
        indicators = ("429", "quota", "throttle", "age", "signature", "extractor")
        return any(token in text for token in indicators)


class LavalinkAudioBackend:
    """Helper for managing a Mafic node and resolving tracks."""

    def __init__(
        self,
        bot,
        *,
        logger: Optional[logging.Logger] = None,
        identifier: str = "primary",
    ) -> None:
        self.bot = bot
        self.logger = logger or _default_logger()
        self.identifier = identifier
        self._ready = asyncio.Event()
        # import mafic lazily
        global mafic
        try:
            import mafic as _mafic

            mafic = _mafic
        except Exception:
            raise RuntimeError("mafic library is required for Lavalink audio backend")

        self._pool = mafic.NodePool(bot)
        self._node: Optional[mafic.Node] = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Setup & lifecycle
    # ------------------------------------------------------------------
    async def connect(self) -> None:
        """Create the Lavalink node if it does not already exist."""

        async with self._lock:
            if self._node is not None:
                return

            host, port, password, secure = get_lavalink_connection_info()
            session_id = os.getenv("LAVALINK_SESSION", "elbot")

            self.logger.info(
                "Connecting to Lavalink", extra={"host": host, "port": port, "secure": secure}
            )

            create_params = inspect.signature(self._pool.create_node).parameters
            node_kwargs = dict(
                host=host,
                port=port,
                label=self.identifier,
                password=password,
                secure=secure,
            )
            if "session_id" in create_params:
                node_kwargs["session_id"] = session_id

            node = await self._pool.create_node(**node_kwargs)
            self._node = node
            self._ready.set()

    async def wait_ready(self, timeout: float = 10.0) -> bool:
        await self.connect()
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return False
        return True

    async def close(self) -> None:
        if self._node:
            try:
                disconnect_coro = getattr(self._node, "disconnect", None)
                if disconnect_coro:
                    await disconnect_coro()
            except Exception:
                pass
        self._node = None
        self._ready.clear()

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------
    def get_player(self, guild_id: int) -> Optional[mafic.Player]:
        return self._pool.get_player(guild_id)

    def fetch_player(self, guild_id: int) -> mafic.Player:
        player = self._pool.get_player(guild_id)
        if player is None:
            raise LavalinkUnavailable("No voice connection for guild")
        return player

    async def resolve_tracks(
        self,
        query: str,
        *,
        prefer_search: bool = True,
    ) -> List[TrackHandle]:
        """Resolve a Lavalink track list from a query or URL."""

        if self._node is None:
            raise LavalinkUnavailable("Lavalink node is not ready")

        search_type = os.getenv("LAVALINK_SEARCH_TYPE", "ytsearch").strip() or "ytsearch"

        legacy_mode = hasattr(self._node, "get_tracks")
        detail = "load_type=unknown"
        tracks: Iterable[mafic.Track]
        fetch_result: mafic.Playlist | list[mafic.Track] | None = None

        try:
            if legacy_mode:
                identifier = query
                if prefer_search and not query.startswith("http"):
                    identifier = f"{search_type}:{query}"
                load_result = await self._node.get_tracks(identifier)
                tracks = getattr(load_result, "tracks", [])
                load_type = getattr(load_result, "load_type", "unknown")
                message = getattr(load_result, "exception", None)
                if message:
                    message = getattr(message, "message", str(message))
                detail = f"load_type={load_type}"
                if message:
                    detail = f"{detail} message={message!s}"
            else:
                fetch_result = await self._node.fetch_tracks(query, search_type=search_type)
        except Exception as exc:  # pragma: no cover - network errors
            raise TrackLoadFailure("Failed to communicate with Lavalink", cause=exc) from exc

        if not legacy_mode:
            if isinstance(fetch_result, mafic.Playlist):
                tracks = fetch_result.tracks
                detail = f"load_type=PLAYLIST name={fetch_result.name!s}"
            elif fetch_result is None:
                tracks = []
                detail = "load_type=NO_MATCHES"
            else:
                tracks = fetch_result
                detail = "load_type=TRACKS"

        if not tracks:
            raise TrackLoadFailure(f"No tracks returned ({detail})")

        return [TrackHandle.from_mafic(track) for track in tracks]

    async def decode_track(self, encoded: str) -> TrackHandle:
        if self._node is None:
            raise LavalinkUnavailable("Lavalink node is not ready")
        track = await self._node.decode_track(encoded)
        return TrackHandle.from_mafic(track)
