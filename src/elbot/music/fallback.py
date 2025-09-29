"""Fallback playback logic with yt-dlp."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Dict, Optional

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
        """Resolve a queued track with adaptive strategy based on system performance."""

        start = time.perf_counter()
        prefer_search = not query.startswith("http")
        
        # Use environment variable to control strategy
        # MUSIC_STRATEGY can be: "lavalink_first" (default), "fallback_first", or "parallel"
        import os
        strategy = os.getenv("MUSIC_STRATEGY", "lavalink_first").lower()
        
        if strategy == "parallel":
            # Try both in parallel and use whichever succeeds first
            return await self._parallel_resolve(
                query,
                requested_by=requested_by,
                requester_display=requester_display,
                channel_id=channel_id,
                prefer_search=prefer_search,
                start_time=start,
            )
        elif strategy == "fallback_first":
            # Original behavior - try fallback first
            return await self._fallback_first_resolve(
                query,
                requested_by=requested_by,
                requester_display=requester_display,
                channel_id=channel_id,
                prefer_search=prefer_search,
                start_time=start,
            )
        else:
            # Default: Try Lavalink first (faster on most systems)
            return await self._lavalink_first_resolve(
                query,
                requested_by=requested_by,
                requester_display=requester_display,
                channel_id=channel_id,
                prefer_search=prefer_search,
                start_time=start,
            )
    
    async def _lavalink_first_resolve(
        self,
        query: str,
        *,
        requested_by: int,
        requester_display: str,
        channel_id: int,
        prefer_search: bool,
        start_time: float,
    ) -> QueuedTrack:
        """Try Lavalink first, fall back to yt-dlp if it fails."""
        
        try:
            handle = await self._resolve_lavalink(query, prefer_search=prefer_search)
            self.metrics.observe_startup((time.perf_counter() - start_time) * 1000)
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
                "Lavalink resolution failed, attempting fallback",
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
                self.metrics.observe_startup((time.perf_counter() - start_time) * 1000)
                return fallback_entry
            except TrackLoadFailure as fallback_exc:
                self.metrics.incr_failed()
                raise fallback_exc from lavalink_exc
    
    async def _fallback_first_resolve(
        self,
        query: str,
        *,
        requested_by: int,
        requester_display: str,
        channel_id: int,
        prefer_search: bool,
        start_time: float,
    ) -> QueuedTrack:
        """Original behavior - try fallback first."""
        
        base_error = TrackLoadFailure("Fallback-first resolution (Lavalink deferred)")
        try:
            fallback_entry = await self._resolve_fallback(
                query,
                requested_by=requested_by,
                requester_display=requester_display,
                channel_id=channel_id,
                base_error=base_error,
            )
            self.metrics.observe_startup((time.perf_counter() - start_time) * 1000)
            return fallback_entry
        except TrackLoadFailure as fallback_exc:
            self.logger.warning(
                "Fallback resolution failed, attempting Lavalink",
                extra={"query": query, "error": str(fallback_exc)},
            )
            try:
                handle = await self._resolve_lavalink(query, prefer_search=prefer_search)
            except TrackLoadFailure as lavalink_exc:
                self.metrics.incr_failed()
                raise lavalink_exc from fallback_exc
            self.metrics.observe_startup((time.perf_counter() - start_time) * 1000)
            return self._build_entry(
                handle,
                query=query,
                requested_by=requested_by,
                requester_display=requester_display,
                channel_id=channel_id,
                is_fallback=False,
                fallback_source=None,
            )
    
    async def _parallel_resolve(
        self,
        query: str,
        *,
        requested_by: int,
        requester_display: str,
        channel_id: int,
        prefer_search: bool,
        start_time: float,
    ) -> QueuedTrack:
        """Try both methods in parallel and use whichever succeeds first."""
        
        import asyncio
        
        async def try_lavalink():
            try:
                handle = await self._resolve_lavalink(query, prefer_search=prefer_search)
                return self._build_entry(
                    handle,
                    query=query,
                    requested_by=requested_by,
                    requester_display=requester_display,
                    channel_id=channel_id,
                    is_fallback=False,
                    fallback_source=None,
                )
            except Exception as e:
                return e
        
        async def try_fallback():
            try:
                return await self._resolve_fallback(
                    query,
                    requested_by=requested_by,
                    requester_display=requester_display,
                    channel_id=channel_id,
                    base_error=TrackLoadFailure("Parallel resolution"),
                )
            except Exception as e:
                return e
        
        # Run both in parallel
        results = await asyncio.gather(
            try_lavalink(),
            try_fallback(),
            return_exceptions=False,
        )
        
        # Check results and return the first success
        for result in results:
            if not isinstance(result, Exception):
                self.metrics.observe_startup((time.perf_counter() - start_time) * 1000)
                return result
        
        # Both failed - raise the Lavalink error as primary
        self.metrics.incr_failed()
        if isinstance(results[0], Exception):
            raise results[0]
        raise TrackLoadFailure("Both resolution methods failed")

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
        selected_source: Optional[str] = None
        handle: list[TrackHandle] | list = []
        for candidate in sources_to_try:
            try:
                handle = await self.backend.resolve_tracks(candidate, prefer_search=False)
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
        track_handle = self._augment_handle_metadata(track_handle, info, selected_source)
        entry = self._build_entry(
            track_handle,
            query=query,
            requested_by=requested_by,
            requester_display=requester_display,
            channel_id=channel_id,
            is_fallback=True,
            fallback_source=selected_source,
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


    def _augment_handle_metadata(
        self, handle: TrackHandle, info: Dict[str, Any], fallback_source: Optional[str]
    ) -> TrackHandle:
        title = str(info.get("title") or info.get("track", "")).strip() or handle.title
        author = str(
            info.get("uploader")
            or info.get("channel")
            or info.get("artist")
            or info.get("creator")
            or handle.author
        ).strip() or handle.author

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


