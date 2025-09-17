"""Lavalink audio backend using Mafic."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Iterable, List, Optional

os.environ.setdefault("MAFIC_LIBRARY", "nextcord")
os.environ.setdefault("MAFIC_IGNORE_LIBRARY_CHECK", "1")

import mafic

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

    track: mafic.Track
    title: str
    author: str
    duration: int
    uri: Optional[str]
    source: str

    @classmethod
    def from_mafic(cls, track: mafic.Track) -> "TrackHandle":
        info = getattr(track, "info", {}) or {}
        return cls(
            track=track,
            title=str(info.get("title") or "Unknown title"),
            author=str(info.get("author") or "Unknown creator"),
            duration=int(info.get("length") or info.get("duration") or 0),
            uri=info.get("uri"),
            source=str(info.get("sourceName") or "unknown"),
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

            host = os.getenv("LAVALINK_HOST", "127.0.0.1")
            port = int(os.getenv("LAVALINK_PORT", "2333"))
            password = os.getenv("LAVALINK_PASSWORD", "youshallnotpass")
            secure = os.getenv("LAVALINK_SSL", "false").lower() == "true"
            session_id = os.getenv("LAVALINK_SESSION", "elbot")

            self.logger.info(
                "Connecting to Lavalink", extra={"host": host, "port": port, "secure": secure}
            )

            node = await self._pool.create_node(
                host=host,
                port=port,
                label=self.identifier,
                password=password,
                secure=secure,
                session_id=session_id,
            )
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
            await self._node.disconnect()
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

        identifier = query
        if prefer_search and not query.startswith("http"):
            identifier = f"ytsearch:{query}"

        try:
            load_result = await self._node.get_tracks(identifier)
        except Exception as exc:  # pragma: no cover - network errors
            raise TrackLoadFailure("Failed to communicate with Lavalink", cause=exc) from exc

        tracks: Iterable[mafic.Track] = getattr(load_result, "tracks", [])
        if not tracks:
            load_type = getattr(load_result, "load_type", "unknown")
            message = getattr(load_result, "exception", None)
            if message:
                message = getattr(message, "message", str(message))
            detail = f"load_type={load_type} message={message!s}" if message else f"load_type={load_type}"
            raise TrackLoadFailure(f"No tracks returned ({detail})")

        return [TrackHandle.from_mafic(track) for track in tracks]

    async def decode_track(self, encoded: str) -> TrackHandle:
        if self._node is None:
            raise LavalinkUnavailable("Lavalink node is not ready")
        track = await self._node.decode_track(encoded)
        return TrackHandle.from_mafic(track)

