"""Fallback playback logic with yt-dlp."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Optional

import yt_dlp

from .audio_backend import LavalinkAudioBackend, TrackHandle, TrackLoadFailure

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

from .cookies import CookieManager
from .metrics import PlaybackMetrics
from .queue import QueuedTrack

__all__ = ["FallbackPlayer"]


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
    ) -> None:
        self.backend = backend
        self.cookies = cookies or CookieManager()
        self.metrics = metrics or PlaybackMetrics()
        self.logger = logger or logging.getLogger("elbot.music.fallback")

    # ------------------------------------------------------------------
    async def build_queue_entry(
        self,
        query: str,
        *,
        requested_by: int,
        requester_display: str,
        channel_id: int,
    ) -> QueuedTrack:
        """Resolve a queued track, resorting to yt-dlp when necessary."""

        start = time.perf_counter()
        prefer_search = not query.startswith("http")
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
        except TrackLoadFailure as first_error:
            self.logger.warning(
                "Lavalink resolve failed, attempting fallback",
                extra={"query": query, "error": str(first_error)},
            )
            fallback_entry = await self._resolve_fallback(
                query,
                requested_by=requested_by,
                requester_display=requester_display,
                channel_id=channel_id,
                base_error=first_error,
            )
            self.metrics.observe_startup((time.perf_counter() - start) * 1000)
            return fallback_entry

    async def _resolve_lavalink(self, query: str, *, prefer_search: bool) -> TrackHandle:
        """Attempt to resolve using Lavalink with retries."""

        attempt = 0
        delay = 0.5
        while True:
            try:
                tracks = await self.backend.resolve_tracks(query, prefer_search=prefer_search)
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
        sources_to_try = []
        primary_stream = info.get("url")
        webpage = info.get("webpage_url")
        if primary_stream:
            sources_to_try.append(primary_stream)
        if webpage and webpage not in sources_to_try:
            sources_to_try.append(webpage)

        if not sources_to_try:
            self.metrics.incr_failed()
            raise TrackLoadFailure("yt-dlp did not yield a usable stream", cause=base_error)

        last_error: Optional[TrackLoadFailure] = None
        handle = []
        for candidate in sources_to_try:
            try:
                handle = await self.backend.resolve_tracks(candidate, prefer_search=False)
            except TrackLoadFailure as exc:
                last_error = exc
                continue
            if handle:
                break

        if not handle:
            self.metrics.incr_failed()
            raise TrackLoadFailure(
                "Fallback stream failed to load",
                cause=last_error or base_error,
            )

        track_handle = handle[0]
        entry = self._build_entry(
            track_handle,
            query=query,
            requested_by=requested_by,
            requester_display=requester_display,
            channel_id=channel_id,
            is_fallback=True,
            fallback_source=stream_url,
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
            raise TrackLoadFailure("yt-dlp extraction failed", cause=exc) from exc

        if "entries" in info:
            # Playlist or search results
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


