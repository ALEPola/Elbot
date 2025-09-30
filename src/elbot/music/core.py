"""Core music playback components."""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import random
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, replace
from typing import Any, Deque, Dict, Iterable, List, Optional

import yt_dlp

from elbot.config import get_lavalink_connection_info

from .support import CookieManager, PlaybackMetrics, SearchCache

os.environ.setdefault("MAFIC_LIBRARY", "nextcord")
os.environ.setdefault("MAFIC_IGNORE_LIBRARY_CHECK", "1")

mafic = None

__all__ = [
    "TrackHandle",
    "QueuedTrack",
    "MusicQueue",
    "LavalinkAudioBackend",
    "LavalinkUnavailable",
    "TrackLoadFailure",
    "FallbackPlayer",
]


def _default_logger() -> logging.Logger:
    return logging.getLogger("elbot.music.lavalink")


@dataclass(slots=True)
class TrackHandle:
    """Light-weight wrapper around a Lavalink track."""

    track: "mafic.Track"  # type: ignore[name-defined]
    title: str
    author: str
    duration: int
    uri: Optional[str]
    source: str

    @classmethod
    def from_mafic(cls, track: "mafic.Track") -> "TrackHandle":
        info = getattr(track, "info", None) or {}

        title = getattr(track, "title", None) or info.get("title") or "Unknown title"
        author = (
            getattr(track, "author", None) or info.get("author") or "Unknown creator"
        )
        duration = (
            getattr(track, "length", None)
            or info.get("length")
            or info.get("duration")
            or 0
        )
        uri = getattr(track, "uri", None) or info.get("uri")
        source = getattr(track, "source", None) or info.get("sourceName") or "unknown"

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

        global mafic
        try:
            import mafic as _mafic

            mafic = _mafic
        except Exception as exc:  # pragma: no cover - import error surface
            raise RuntimeError(
                "mafic library is required for Lavalink audio backend"
            ) from exc

        self._pool = mafic.NodePool(bot)
        self._node: Optional["mafic.Node"] = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        """Create the Lavalink node if it does not already exist."""

        async with self._lock:
            if self._node is not None:
                return

            host, port, password, secure = get_lavalink_connection_info()
            session_id = os.getenv("LAVALINK_SESSION", "elbot")

            self.logger.info(
                "Connecting to Lavalink",
                extra={"host": host, "port": port, "secure": secure},
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
            await self._node.disconnect()
        self._node = None
        self._ready.clear()

    def get_player(self, guild_id: int) -> Optional["mafic.Player"]:
        return self._pool.get_player(guild_id)

    def fetch_player(self, guild_id: int) -> "mafic.Player":
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

        search_type = (
            os.getenv("LAVALINK_SEARCH_TYPE", "ytsearch").strip() or "ytsearch"
        )

        legacy_mode = hasattr(self._node, "get_tracks")
        detail = "load_type=unknown"
        tracks: Iterable["mafic.Track"]
        fetch_result: "mafic.Playlist | list[mafic.Track] | None" = None

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
                fetch_result = await self._node.fetch_tracks(
                    query, search_type=search_type
                )
        except Exception as exc:  # pragma: no cover - network errors
            raise TrackLoadFailure(
                "Failed to communicate with Lavalink", cause=exc
            ) from exc

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


@dataclass(slots=True)
class QueuedTrack:
    """Metadata stored in the queue."""

    id: str
    handle: TrackHandle
    query: str
    channel_id: int
    requested_by: int
    requester_display: str
    is_fallback: bool = False
    fallback_source: Optional[str] = None
    queued_message_id: Optional[int] = None

    def clone(self) -> "QueuedTrack":
        return replace(self, id=uuid.uuid4().hex)


class MusicQueue:
    """A thread-safe deque with atomic helpers."""

    def __init__(self) -> None:
        self._queue: Deque[QueuedTrack] = deque()
        self._lock = threading.Lock()
        self._last_played: Optional[QueuedTrack] = None

    def __len__(self) -> int:
        with self._lock:
            return len(self._queue)

    def _insert_at_locked(self, index: int, track: QueuedTrack) -> None:
        if not self._queue or index <= 0:
            self._queue.appendleft(track)
            return
        size = len(self._queue)
        if index >= size:
            self._queue.append(track)
            return
        self._queue.rotate(-index)
        self._queue.appendleft(track)
        self._queue.rotate(index)

    def snapshot(self) -> List[QueuedTrack]:
        with self._lock:
            return list(self._queue)

    def add(self, track: QueuedTrack) -> None:
        with self._lock:
            self._queue.append(track)

    def add_next(self, track: QueuedTrack) -> None:
        with self._lock:
            self._queue.appendleft(track)

    def pop_next(self) -> Optional[QueuedTrack]:
        with self._lock:
            if not self._queue:
                return None
            track = self._queue.popleft()
            self._last_played = track
            return track

    def peek(self, index: int = 0) -> Optional[QueuedTrack]:
        with self._lock:
            if index < 0 or index >= len(self._queue):
                return None
            try:
                return self._queue[index]
            except IndexError:
                return None

    def clear(self) -> None:
        with self._lock:
            self._queue.clear()

    def remove_index(self, index: int) -> Optional[QueuedTrack]:
        with self._lock:
            size = len(self._queue)
            if index < 0 or index >= size:
                return None
            self._queue.rotate(-index)
            track = self._queue.popleft()
            self._queue.rotate(index)
            return track

    def remove_range(self, start: int, end: int) -> List[QueuedTrack]:
        with self._lock:
            size = len(self._queue)
            if size == 0:
                return []
            start = max(start, 0)
            end = min(end, size - 1)
            if start > end:
                return []
            count = end - start + 1
            removed: List[QueuedTrack] = []
            self._queue.rotate(-start)
            for _ in range(count):
                removed.append(self._queue.popleft())
            self._queue.rotate(start)
            return removed

    def move(self, source_index: int, dest_index: int) -> bool:
        with self._lock:
            size = len(self._queue)
            if size == 0 or source_index < 0 or source_index >= size:
                return False
            dest_index = max(0, min(dest_index, size - 1))
            if source_index == dest_index:
                return True
            self._queue.rotate(-source_index)
            track = self._queue.popleft()
            self._queue.rotate(source_index)
            insert_index = min(dest_index, len(self._queue))
            self._insert_at_locked(insert_index, track)
            return True

    def shuffle(self) -> None:
        with self._lock:
            items = list(self._queue)
            random.shuffle(items)
            self._queue = deque(items)

    def replay_last(self) -> Optional[QueuedTrack]:
        with self._lock:
            if not self._last_played:
                return None
            replay_track = self._last_played.clone()
            self._queue.appendleft(replay_track)
            return replay_track

    def __iter__(self) -> Iterable[QueuedTrack]:
        return iter(self.snapshot())


_KNOWN_YTDLP_PREFIXES = (
    "ytsearch:",
    "ytsearch1:",
    "ytsearch5:",
    "ytdsearch:",
    "ytsearch10:",
    "spsearch:",
    "scsearch:",
)


def _normalise_query(query: str) -> str:
    stripped = query.strip()
    lower = stripped.lower()
    if lower.startswith(("http://", "https://")):
        return stripped
    if any(lower.startswith(prefix) for prefix in _KNOWN_YTDLP_PREFIXES):
        return stripped
    return f"ytsearch:{stripped}"


class FallbackPlayer:
    """Resolve tracks with Lavalink and gracefully fall back to yt-dlp."""

    RETRYABLE_ERRORS = ("429", "throttle", "quota", "sign in", "sign-in", "age")

    def __init__(
        self,
        backend: LavalinkAudioBackend,
        *,
        cookies: Optional[CookieManager] = None,
        metrics: Optional[PlaybackMetrics] = None,
        logger: Optional[logging.Logger] = None,
        search_cache: Optional[SearchCache] = None,
    ) -> None:
        self.backend = backend
        self.cookies = cookies or CookieManager()
        self.metrics = metrics or PlaybackMetrics()
        self.logger = logger or logging.getLogger("elbot.music.fallback")
        self.cache = search_cache or SearchCache(persist=False)

    async def build_queue_entry(
        self,
        query: str,
        *,
        requested_by: int,
        requester_display: str,
        channel_id: int,
    ) -> QueuedTrack:
        """Resolve a queued track, trying Lavalink first for better performance."""

        start = time.perf_counter()
        prefer_search = not query.startswith("http")

        cached_entry = await self._resolve_cached(
            query,
            requested_by=requested_by,
            requester_display=requester_display,
            channel_id=channel_id,
        )
        if cached_entry is not None:
            self.metrics.observe_startup((time.perf_counter() - start) * 1000)
            return cached_entry

        try:
            handle = await self._resolve_lavalink(query, prefer_search=prefer_search)
            self.metrics.observe_startup((time.perf_counter() - start) * 1000)
            return self._build_entry(
                handle,
                query=query,
                requested_by=requested_by,
                requester_display=requester_display,
                channel_id=channel_id,
                is_fallback=False,
                fallback_source=None,
            )
        except TrackLoadFailure as lavalink_exc:
            self.logger.warning(
                "Lavalink resolution failed, attempting yt-dlp fallback",
                extra={"query": query, "error": str(lavalink_exc)},
            )

            try:
                fallback_entry = await self._resolve_fallback(
                    query,
                    requested_by=requested_by,
                    requester_display=requester_display,
                    channel_id=channel_id,
                    base_error=lavalink_exc,
                )
                self.metrics.observe_startup((time.perf_counter() - start) * 1000)
                return fallback_entry
            except TrackLoadFailure as fallback_exc:
                self.metrics.incr_failed()
                raise TrackLoadFailure(
                    f"Both Lavalink and yt-dlp failed: {fallback_exc}",
                    cause=lavalink_exc,
                ) from fallback_exc

    async def build_fallback_entry(
        self,
        query: str,
        *,
        requested_by: int,
        requester_display: str,
        channel_id: int,
        base_error: TrackLoadFailure,
    ) -> QueuedTrack:
        """Directly build a fallback entry without retrying Lavalink."""

        cached_entry = await self._resolve_cached(
            query,
            requested_by=requested_by,
            requester_display=requester_display,
            channel_id=channel_id,
        )
        if cached_entry is not None:
            return cached_entry

        self.logger.info(
            "Attempting direct fallback resolution",
            extra={"query": query, "requested_by": requested_by},
        )
        return await self._resolve_fallback(
            query,
            requested_by=requested_by,
            requester_display=requester_display,
            channel_id=channel_id,
            base_error=base_error,
        )


    async def _resolve_cached(
        self,
        query: str,
        *,
        requested_by: int,
        requester_display: str,
        channel_id: int,
    ) -> Optional[QueuedTrack]:
        if not self.cache:
            return None
        cached = self.cache.get(query)
        if not cached:
            return None

        last_error: Optional[Exception] = None
        for candidate in cached.sources:
            try:
                handles = await self.backend.resolve_tracks(
                    candidate, prefer_search=False
                )
            except TrackLoadFailure as exc:
                last_error = exc
                self.logger.debug(
                    "Cached candidate failed",
                    extra={
                        "query": query,
                        "candidate": candidate,
                        "error": str(exc),
                    },
                )
                continue
            if not handles:
                self.logger.debug(
                    "Cached candidate yielded no tracks",
                    extra={"query": query, "candidate": candidate},
                )
                continue
            track_handle = handles[0]
            entry = self._build_entry(
                track_handle,
                query=query,
                requested_by=requested_by,
                requester_display=requester_display,
                channel_id=channel_id,
                is_fallback=True,
                fallback_source=candidate,
            )
            self.metrics.incr_fallback()
            self.metrics.record_fallback_source(candidate)
            self.logger.info(
                "Resolved stream from cache",
                extra={
                    "query": query,
                    "candidate": candidate,
                    "identifier": cached.identifier,
                    "requested_by": requested_by,
                },
            )
            return entry

        self.cache.evict(query)
        self.logger.warning(
            "Cached entry invalidated",
            extra={
                "query": query,
                "identifier": cached.identifier,
                "error": str(last_error) if last_error else None,
            },
        )
        return None

    async def _resolve_lavalink(
        self, query: str, *, prefer_search: bool
    ) -> TrackHandle:
        attempt = 0
        delay = 0.5
        while True:
            try:
                tracks = await self.backend.resolve_tracks(
                    query, prefer_search=prefer_search
                )
            except TrackLoadFailure as exc:
                attempt += 1
                if not exc.is_retryable or attempt >= 3:
                    raise
                await asyncio.sleep(delay)
                delay *= 2
                continue
            if not tracks:
                raise TrackLoadFailure("No tracks returned")
            return tracks[0]

    async def _resolve_fallback(
        self,
        query: str,
        *,
        requested_by: int,
        requester_display: str,
        channel_id: int,
        base_error: TrackLoadFailure,
    ) -> QueuedTrack:
        self.metrics.incr_fallback()
        info = await self._extract_with_yt_dlp(query, base_error=base_error)
        sources_to_try: List[str] = []
        primary_stream = info.get("url")
        webpage = info.get("webpage_url")
        if primary_stream:
            sources_to_try.append(primary_stream)
        if webpage and webpage not in sources_to_try:
            sources_to_try.append(webpage)

        if not sources_to_try:
            self.metrics.incr_failed()
            raise TrackLoadFailure(
                "yt-dlp did not yield a usable stream", cause=base_error
            )

        last_error: Optional[TrackLoadFailure] = None
        selected_source: Optional[str] = None
        handle: list[TrackHandle] = []
        for candidate in sources_to_try:
            try:
                handle = await self.backend.resolve_tracks(
                    candidate, prefer_search=False
                )
            except TrackLoadFailure as exc:
                last_error = exc
                self.logger.warning(
                    "Fallback candidate failed",
                    extra={"candidate": candidate, "error": str(exc)},
                )
                continue
            if not handle:
                self.logger.warning(
                    "Fallback candidate produced no tracks",
                    extra={"candidate": candidate},
                )
                continue
            selected_source = candidate
            break

        if not handle or selected_source is None:
            self.metrics.incr_failed()
            raise TrackLoadFailure(
                "Fallback stream failed to load",
                cause=last_error or base_error,
            )

        track_handle = handle[0]
        track_handle = self._augment_handle_metadata(
            track_handle, info, selected_source
        )
        entry = self._build_entry(
            track_handle,
            query=query,
            requested_by=requested_by,
            requester_display=requester_display,
            channel_id=channel_id,
            is_fallback=True,
            fallback_source=selected_source,
        )
        if self.cache:
            identifier = None
            if isinstance(info, dict):
                raw_identifier = info.get("id")
                if raw_identifier:
                    candidate = str(raw_identifier).strip()
                    if candidate:
                        identifier = candidate
            try:
                self.cache.remember(
                    query,
                    sources=sources_to_try,
                    identifier=identifier,
                )
            except Exception as exc:
                self.logger.debug(
                    "Failed to update search cache",
                    extra={"query": query, "error": str(exc)},
                )
        self.metrics.record_fallback_source(selected_source)
        self.logger.info(
            "Resolved fallback stream",
            extra={
                "query": query,
                "selected_source": selected_source,
                "requested_by": requested_by,
            },
        )
        return entry

    async def _extract_with_yt_dlp(
        self, query: str, *, base_error: TrackLoadFailure
    ) -> dict:
        options = self.cookies.yt_dlp_options()
        options.update({"skip_download": True})

        query_for_dl = _normalise_query(query)

        def _do_extract() -> dict:
            with yt_dlp.YoutubeDL(options) as ydl:
                return ydl.extract_info(query_for_dl, download=False)

        try:
            info = await asyncio.to_thread(_do_extract)
        except Exception as exc:  # pragma: no cover - network/yt-dlp errors
            category = self._categorize_exception(exc)
            self.metrics.record_extractor_failure(category)
            self.metrics.incr_failed()
            lowered = str(exc).lower()
            if "sign in to confirm you" in lowered:
                cookie_path = self.cookies.cookie_file()
                self.logger.error(
                    "YouTube rejected unauthenticated request; configure YT_COOKIES_FILE with fresh export",
                    extra={
                        "query": query,
                        "cookie_configured": bool(cookie_path and cookie_path.exists()),
                        "cookie_path": str(cookie_path) if cookie_path else None,
                    },
                )
                message = (
                    "YouTube rejected unauthenticated playback. "
                    "Export fresh cookies and set YT_COOKIES_FILE or YTDLP_COOKIES_FILE."
                )
                raise TrackLoadFailure(message, cause=exc) from exc
            raise TrackLoadFailure("yt-dlp extraction failed", cause=exc) from exc

        if "entries" in info:
            entries = info.get("entries") or []
            if not entries:
                self.metrics.incr_failed()
                raise TrackLoadFailure("yt-dlp returned no entries", cause=base_error)
            info = entries[0]
        return info

    def _categorize_exception(self, exc: Exception) -> str:
        text = str(exc).lower()
        for indicator in self.RETRYABLE_ERRORS:
            if indicator in text:
                return indicator
        return exc.__class__.__name__.lower()

    def _build_entry(
        self,
        handle: TrackHandle,
        *,
        query: str,
        requested_by: int,
        requester_display: str,
        channel_id: int,
        is_fallback: bool,
        fallback_source: Optional[str],
    ) -> QueuedTrack:
        entry = QueuedTrack(
            id=uuid.uuid4().hex,
            handle=handle,
            query=query,
            channel_id=channel_id,
            requested_by=requested_by,
            requester_display=requester_display,
            is_fallback=is_fallback,
            fallback_source=fallback_source,
        )
        return entry

    def _augment_handle_metadata(
        self, handle: TrackHandle, info: Dict[str, Any], fallback_source: Optional[str]
    ) -> TrackHandle:
        title = str(info.get("title") or info.get("track", "")).strip() or handle.title
        author = (
            str(
                info.get("uploader")
                or info.get("channel")
                or info.get("artist")
                or info.get("creator")
                or handle.author
            ).strip()
            or handle.author
        )

        duration = handle.duration
        for key in ("duration", "length", "approx_duration_ms"):
            value = info.get(key)
            if value is None:
                continue
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                continue
            if numeric <= 0:
                continue
            if key == "approx_duration_ms" or numeric >= 10_000:
                duration = int(numeric)
            else:
                duration = int(numeric * 1000)
            break

        uri = handle.uri
        for key in ("webpage_url", "original_url", "url"):
            candidate = info.get(key)
            if isinstance(candidate, str) and candidate:
                uri = uri or candidate
                break

        source = handle.source
        if source in ("unknown", "http") and fallback_source:
            source = "http" if fallback_source.startswith("http") else source

        if (
            title == handle.title
            and author == handle.author
            and duration == handle.duration
            and uri == handle.uri
            and source == handle.source
        ):
            return handle

        return TrackHandle(
            track=handle.track,
            title=title,
            author=author,
            duration=duration,
            uri=uri,
            source=source,
        )
