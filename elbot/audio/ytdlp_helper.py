"""Utilities for resolving YouTube streams via yt-dlp.

This module keeps all yt-dlp interaction in one place so the extractor
can be swapped or upgraded independently from the Discord cog.  The
extractor is intentionally lightweight: it only exposes the information
the music cog needs to build a Lavalink-compatible track fallback.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any

import yt_dlp
from yt_dlp.utils import DownloadError

__all__ = (
    "ExtractionError",
    "YTDLPConfig",
    "YTDLPExtractor",
    "YTDLPResult",
)

logger = logging.getLogger("elbot.audio.ytdlp")


class ExtractionError(RuntimeError):
    """Raised when yt-dlp is unable to resolve a stream."""


@dataclass(slots=True)
class YTDLPConfig:
    """Runtime configuration for :class:`YTDLPExtractor`.

    Attributes
    ----------
    cookies_file:
        Optional path to a Netscape-style cookie jar.  Providing cookies
        allows yt-dlp to access age-restricted or region-locked content.
    custom_options:
        Additional yt-dlp keyword arguments merged into the defaults.
    """

    cookies_file: Path | None = None
    custom_options: dict[str, Any] | None = None


@dataclass(slots=True)
class YTDLPResult:
    """A minimal representation of a resolved YouTube stream."""

    stream_url: str
    title: str
    webpage_url: str
    duration: int | None
    thumbnail: str | None


class YTDLPExtractor:
    """A thin asynchronous wrapper around yt-dlp."""

    __slots__ = ("_config",)

    def __init__(self, config: YTDLPConfig | None = None) -> None:
        self._config = config or YTDLPConfig()

    def _build_options(self) -> dict[str, Any]:
        """Return the yt-dlp options for the next extraction."""

        opts: dict[str, Any] = {
            # bestaudio/best ensures opus/webm when available which Lavalink
            # can remux without unnecessary transcoding.
            "format": "bestaudio/best",
            "quiet": True,
            "noplaylist": True,
            "nocheckcertificate": True,
            "skip_download": True,
            "default_search": "ytsearch",
            # Allow re-use of cached requests to avoid unnecessary throttling.
            "cachedir": str(Path.home() / ".cache" / "elbot" / "yt-dlp"),
        }

        if self._config.cookies_file:
            opts["cookiefile"] = str(self._config.cookies_file)

        if self._config.custom_options:
            opts.update(self._config.custom_options)

        return opts

    async def extract(self, query: str) -> YTDLPResult:
        """Resolve *query* into a :class:`YTDLPResult`.

        The heavy lifting is performed in a thread so the async event loop
        stays responsive.
        """

        def _extract() -> YTDLPResult:
            opts = self._build_options()
            logger.debug("yt-dlp extracting %s", query)
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(query, download=False)
            except DownloadError as exc:  # pragma: no cover - exercised via tests
                raise ExtractionError(str(exc)) from exc

            if "entries" in info:
                # Some queries (e.g. playlists) still return a list even when
                # ``noplaylist`` is active.  We grab the first playable entry.
                entries = info.get("entries") or []
                info = next((entry for entry in entries if entry), None)

            if not info:
                raise ExtractionError("No playable formats were returned by yt-dlp.")

            fmt = info.get("url")
            if not fmt:
                raise ExtractionError("yt-dlp could not determine a stream URL.")

            return YTDLPResult(
                stream_url=fmt,
                title=info.get("title") or "Unknown title",
                webpage_url=info.get("webpage_url") or query,
                duration=info.get("duration"),
                thumbnail=info.get("thumbnail"),
            )

        return await asyncio.to_thread(_extract)

    def update_config(self, *, cookies_file: Path | None = None, options: dict[str, Any] | None = None) -> None:
        """Update runtime configuration for the extractor.

        Parameters
        ----------
        cookies_file:
            Optional new cookie path.
        options:
            Optional dictionary of yt-dlp keyword arguments that override the
            extractor defaults.
        """

        if cookies_file is not None:
            self._config.cookies_file = cookies_file

        if options:
            merged = dict(self._config.custom_options or {})
            merged.update(options)
            self._config.custom_options = merged

