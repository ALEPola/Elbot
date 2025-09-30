"""Support utilities for the music subsystem."""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import sys
import threading
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence

import aiohttp
import nextcord
import yt_dlp

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .core import QueuedTrack

__all__ = [
    "CookieManager",
    "SearchCache",
    "PlaybackMetrics",
    "EmbedFactory",
    "QueuePaginator",
    "DiagnosticsReport",
    "DiagnosticsService",
    "configure_json_logging",
]


class CookieManager:
    """Monitor and lazily reload YouTube cookies."""

    def __init__(self, *, env_var: str = "YT_COOKIES_FILE") -> None:
        self.env_var = env_var
        self._lock = threading.Lock()
        self._path: Optional[Path] = None
        self._mtime: Optional[float] = None
        self._last_check: float = 0.0
        self._load_from_env()

    def _load_from_env(self) -> None:
        path = os.getenv(self.env_var)
        if not path:
            self._path = None
            self._mtime = None
            return
        resolved = Path(path).expanduser().resolve()
        if not resolved.exists():
            self._path = resolved
            self._mtime = None
        else:
            self._path = resolved
            self._mtime = resolved.stat().st_mtime

    def _refresh_if_needed(self) -> None:
        with self._lock:
            now = time.monotonic()
            if now - self._last_check < 1.0:
                return
            self._last_check = now
            path_from_env = os.getenv(self.env_var)
            if path_from_env:
                resolved = Path(path_from_env).expanduser().resolve()
                if self._path is None or resolved != self._path:
                    self._path = resolved
                    self._mtime = (
                        resolved.stat().st_mtime if resolved.exists() else None
                    )
            if self._path is None:
                return
            if self._path.exists():
                mtime = self._path.stat().st_mtime
                if self._mtime != mtime:
                    self._mtime = mtime
            else:
                self._mtime = None

    def cookie_file(self) -> Optional[Path]:
        self._refresh_if_needed()
        return self._path

    def yt_dlp_options(self) -> Dict[str, object]:
        self._refresh_if_needed()
        options: Dict[str, object] = {
            "quiet": True,
            "format": "bestaudio/best",
            "noplaylist": True,
        }
        if self._path and self._path.exists():
            options["cookiefile"] = str(self._path)
        return options

    def cookie_age_seconds(self) -> Optional[float]:
        self._refresh_if_needed()
        if self._path is None or self._mtime is None:
            return None
        return max(0.0, time.time() - self._mtime)


_CACHE_LOGGER = logging.getLogger("elbot.music.cache")


@dataclass
class CacheRecord:
    key: str
    query: str
    sources: List[str]
    identifier: Optional[str]
    created_at: float
    ttl: int
    last_used: float

    def expired(self, *, now: Optional[float] = None) -> bool:
        if now is None:
            now = time.time()
        return now >= self.created_at + self.ttl

    def to_json(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "query": self.query,
            "sources": self.sources,
            "identifier": self.identifier,
            "created_at": self.created_at,
            "ttl": self.ttl,
            "last_used": self.last_used,
        }

    @classmethod
    def from_json(cls, payload: Dict[str, Any]) -> Optional["CacheRecord"]:
        if not isinstance(payload, dict):
            return None
        key = str(payload.get("key") or "").strip()
        if not key:
            return None
        raw_sources = payload.get("sources") or []
        sources = []
        for item in raw_sources:
            if isinstance(item, str):
                stripped = item.strip()
                if stripped and stripped not in sources:
                    sources.append(stripped)
        if not sources:
            return None
        identifier = payload.get("identifier")
        if identifier is not None:
            identifier = str(identifier).strip() or None
        try:
            created_at = float(payload.get("created_at") or time.time())
            ttl = int(payload.get("ttl") or 0)
            last_used = float(payload.get("last_used") or created_at)
        except (TypeError, ValueError):
            return None
        if ttl <= 0:
            return None
        query = str(payload.get("query") or key)
        return cls(
            key=key,
            query=query,
            sources=sources,
            identifier=identifier,
            created_at=created_at,
            ttl=ttl,
            last_used=last_used,
        )


class SearchCache:
    """Persistent cache for resolved stream URLs with TTL pruning."""

    VERSION = 1

    def __init__(
        self,
        *,
        path: Optional[Path] = None,
        default_ttl: Optional[int] = None,
        max_entries: int = 400,
        persist: bool = True,
    ) -> None:
        self.persist = persist
        env_path = os.getenv("ELBOT_MUSIC_CACHE_FILE", "").strip() if persist else ""
        base_path: Optional[Path] = None
        if persist:
            if path is not None:
                base_path = Path(path).expanduser()
            elif env_path:
                base_path = Path(env_path).expanduser()
            else:
                base_path = Path("chat_history/music_search_cache.json").expanduser()
        self.path: Optional[Path] = base_path

        env_ttl = os.getenv("ELBOT_MUSIC_CACHE_TTL", "").strip()
        ttl = default_ttl if default_ttl is not None else 6 * 3600
        if env_ttl:
            try:
                ttl = max(60, int(env_ttl))
            except ValueError:
                _CACHE_LOGGER.warning("Invalid ELBOT_MUSIC_CACHE_TTL: %s", env_ttl)
        self.default_ttl = ttl

        env_size = os.getenv("ELBOT_MUSIC_CACHE_SIZE", "").strip()
        if env_size:
            try:
                max_entries = max(10, int(env_size))
            except ValueError:
                _CACHE_LOGGER.warning("Invalid ELBOT_MUSIC_CACHE_SIZE: %s", env_size)
        self.max_entries = max_entries

        self._lock = threading.Lock()
        self._entries: Dict[str, CacheRecord] = {}
        self._load()

    def get(self, query: str) -> Optional[CacheRecord]:
        key = self._key(query)
        with self._lock:
            record = self._entries.get(key)
            if record is None:
                return None
            now = time.time()
            if record.expired(now=now):
                del self._entries[key]
                self._persist_locked()
                return None
            record.last_used = now
            return CacheRecord(
                key=record.key,
                query=record.query,
                sources=list(record.sources),
                identifier=record.identifier,
                created_at=record.created_at,
                ttl=record.ttl,
                last_used=record.last_used,
            )

    def remember(
        self,
        query: str,
        *,
        sources: Sequence[str],
        identifier: Optional[str] = None,
        ttl: Optional[int] = None,
    ) -> None:
        cleaned = []
        for item in sources:
            if not isinstance(item, str):
                continue
            stripped = item.strip()
            if stripped and stripped not in cleaned:
                cleaned.append(stripped)
        if not cleaned:
            return
        now = time.time()
        ttl_value = int(ttl or self.default_ttl)
        ttl_value = max(60, ttl_value)
        key = self._key(query)
        identifier_value: Optional[str] = None
        if identifier is not None:
            candidate = str(identifier).strip()
            if candidate:
                identifier_value = candidate
        record = CacheRecord(
            key=key,
            query=query,
            sources=cleaned,
            identifier=identifier_value,
            created_at=now,
            ttl=ttl_value,
            last_used=now,
        )
        with self._lock:
            self._entries[key] = record
            self._prune_locked(now=now)
            self._persist_locked()

    def evict(self, query: str) -> None:
        key = self._key(query)
        with self._lock:
            if key in self._entries:
                del self._entries[key]
                self._persist_locked()

    def clear(self) -> None:
        with self._lock:
            if not self._entries:
                return
            self._entries.clear()
            self._persist_locked()

    def size(self) -> int:
        with self._lock:
            return len(self._entries)

    def _load(self) -> None:
        if not self.persist or self.path is None or not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text("utf-8"))
        except Exception as exc:
            _CACHE_LOGGER.warning("Failed to load music cache %s: %s", self.path, exc)
            return
        entries = payload.get("entries")
        if not isinstance(entries, list):
            return
        now = time.time()
        with self._lock:
            for item in entries:
                record = CacheRecord.from_json(item)
                if not record:
                    continue
                if record.expired(now=now):
                    continue
                key = record.key
                self._entries[key] = record
            self._prune_locked(now=now)

    def _persist_locked(self) -> None:
        if not self.persist or self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        payload = {
            "version": self.VERSION,
            "saved_at": time.time(),
            "entries": [record.to_json() for record in self._entries.values()],
        }
        tmp_path = self.path.with_suffix(".tmp")
        try:
            tmp_path.write_text(
                json.dumps(payload, ensure_ascii=True, indent=2),
                encoding="utf-8",
            )
            tmp_path.replace(self.path)
        except Exception as exc:
            _CACHE_LOGGER.warning("Failed to persist music cache %s: %s", self.path, exc)
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass

    def _prune_locked(self, *, now: Optional[float] = None) -> None:
        if now is None:
            now = time.time()
        expired_keys = [key for key, record in self._entries.items() if record.expired(now=now)]
        for key in expired_keys:
            del self._entries[key]
        if len(self._entries) <= self.max_entries:
            return
        sorted_items = sorted(
            self._entries.items(), key=lambda item: item[1].last_used
        )
        excess = len(self._entries) - self.max_entries
        for key, _ in sorted_items[:excess]:
            del self._entries[key]

    def _key(self, query: str) -> str:
        text = str(query or "").strip()
        if not text:
            return ""
        if "://" in text:
            return text
        lowered = text.lower()
        return " ".join(lowered.split())




@dataclass
class _Average:
    total_ms: float = 0.0
    samples: int = 0

    def add(self, value: float) -> None:
        self.total_ms += value
        self.samples += 1

    @property
    def mean(self) -> float:
        if self.samples == 0:
            return 0.0
        return self.total_ms / self.samples


class PlaybackMetrics:
    """Store counters for playback health."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.plays_started = 0
        self.plays_failed = 0
        self.fallback_used = 0
        self._avg = _Average()
        self.extractor_failures = Counter()
        self.last_fallback_source: Optional[str] = None

    def incr_started(self) -> None:
        with self._lock:
            self.plays_started += 1

    def incr_failed(self) -> None:
        with self._lock:
            self.plays_failed += 1

    def incr_fallback(self) -> None:
        with self._lock:
            self.fallback_used += 1

    def observe_startup(self, ms: float) -> None:
        with self._lock:
            self._avg.add(ms)

    def record_extractor_failure(self, category: str) -> None:
        with self._lock:
            self.extractor_failures[category] += 1

    def record_fallback_source(self, source: str) -> None:
        with self._lock:
            self.last_fallback_source = source

    def snapshot(self) -> Dict[str, object]:
        with self._lock:
            return {
                "plays_started": self.plays_started,
                "plays_failed": self.plays_failed,
                "fallback_used": self.fallback_used,
                "avg_startup_ms": round(self._avg.mean, 2),
                "extractor_failures_by_type": dict(self.extractor_failures),
                "last_fallback_source": self.last_fallback_source,
            }


def _format_duration(ms: int) -> str:
    seconds = max(0, int(ms // 1000))
    return str(dt.timedelta(seconds=seconds))


def _format_eta(ms: int) -> str:
    if ms <= 0:
        return "Ready"
    seconds = int(ms // 1000)
    return f"{seconds // 60}m {seconds % 60}s"


class EmbedFactory:
    """Create embeds for playback events."""

    def __init__(self, *, color: int = 0x5865F2) -> None:
        self.color = color

    def now_playing(
        self,
        track: "QueuedTrack",
        *,
        position: int = 0,
        eta_ms: int = 0,
    ) -> nextcord.Embed:
        info = track.handle
        embed = nextcord.Embed(title="Now Playing", color=self.color)
        embed.description = f"[{info.title}]({info.uri or track.query})"
        embed.add_field(name="Channel", value=info.author or "Unknown", inline=True)
        embed.add_field(
            name="Duration", value=_format_duration(info.duration), inline=True
        )
        embed.add_field(name="Requested by", value=track.requester_display, inline=True)
        embed.add_field(name="Queue position", value=str(position), inline=True)
        embed.add_field(name="ETA", value=_format_eta(eta_ms), inline=True)
        embed.set_footer(text="Fallback" if track.is_fallback else "Lavalink")
        return embed

    def queued(
        self,
        track: "QueuedTrack",
        *,
        position: int,
        eta_ms: int,
    ) -> nextcord.Embed:
        info = track.handle
        embed = nextcord.Embed(title="Track queued", color=self.color)
        embed.description = f"[{info.title}]({info.uri or track.query})"
        embed.add_field(name="Channel", value=info.author or "Unknown", inline=True)
        embed.add_field(
            name="Duration", value=_format_duration(info.duration), inline=True
        )
        embed.add_field(name="Queue position", value=str(position), inline=True)
        embed.add_field(name="Estimated time", value=_format_eta(eta_ms), inline=True)
        embed.set_footer(text=f"Requested by {track.requester_display}")
        return embed

    def queue_page(
        self,
        tracks: Sequence["QueuedTrack"],
        *,
        page: int,
        per_page: int,
        total: int,
        now_playing: Optional["QueuedTrack"] = None,
    ) -> nextcord.Embed:
        embed = nextcord.Embed(title="Queue", color=self.color)
        if now_playing:
            embed.add_field(
                name="Now Playing",
                value=f"[{now_playing.handle.title}]({now_playing.handle.uri or now_playing.query})",
                inline=False,
            )
        if not tracks:
            embed.description = "Queue is empty."
        else:
            lines: List[str] = []
            for index, track in enumerate(tracks, start=1 + page * per_page):
                info = track.handle
                duration = _format_duration(info.duration)
                lines.append(
                    f"`{index}.` [{info.title}]({info.uri or track.query}) — {duration}"
                )
            embed.description = "\n".join(lines)
        embed.set_footer(
            text=f"Page {page + 1}/{max(1, (total + per_page - 1) // per_page)}"
        )
        return embed

    def failure(self, message: str) -> nextcord.Embed:
        return nextcord.Embed(
            title="Playback failed", description=message, color=0xFF5555
        )


class QueuePaginator(nextcord.ui.View):
    """Simple button-based pagination for queue embeds."""

    def __init__(
        self,
        factory: EmbedFactory,
        tracks: Sequence["QueuedTrack"],
        *,
        per_page: int = 8,
        now_playing: Optional["QueuedTrack"] = None,
    ) -> None:
        super().__init__(timeout=60)
        self.factory = factory
        self.tracks = list(tracks)
        self.per_page = per_page
        self.now_playing = now_playing
        self.page = 0
        self.message: Optional[nextcord.Message] = None
        self._update_buttons()

    def _update_buttons(self) -> None:
        total_pages = max(1, (len(self.tracks) + self.per_page - 1) // self.per_page)
        self.first_button.disabled = self.page == 0
        self.prev_button.disabled = self.page == 0
        self.next_button.disabled = self.page >= total_pages - 1
        self.last_button.disabled = self.page >= total_pages - 1

    def _current_slice(self) -> Sequence["QueuedTrack"]:
        start = self.page * self.per_page
        end = start + self.per_page
        return self.tracks[start:end]

    async def send_initial(self, interaction: nextcord.Interaction) -> None:
        embed = self.factory.queue_page(
            self._current_slice(),
            page=self.page,
            per_page=self.per_page,
            total=len(self.tracks),
            now_playing=self.now_playing,
        )
        if interaction.response.is_done():
            self.message = await interaction.followup.send(embed=embed, view=self)
        else:
            self.message = await interaction.send(embed=embed, view=self)

    async def update_message(self) -> None:
        if not self.message:
            return
        embed = self.factory.queue_page(
            self._current_slice(),
            page=self.page,
            per_page=self.per_page,
            total=len(self.tracks),
            now_playing=self.now_playing,
        )
        await self.message.edit(embed=embed, view=self)

    @nextcord.ui.button(label="≪", style=nextcord.ButtonStyle.secondary)
    async def first_button(
        self, _: nextcord.ui.Button, interaction: nextcord.Interaction
    ) -> None:
        self.page = 0
        self._update_buttons()
        await interaction.response.defer()
        await self.update_message()

    @nextcord.ui.button(label="‹", style=nextcord.ButtonStyle.secondary)
    async def prev_button(
        self, _: nextcord.ui.Button, interaction: nextcord.Interaction
    ) -> None:
        self.page = max(0, self.page - 1)
        self._update_buttons()
        await interaction.response.defer()
        await self.update_message()

    @nextcord.ui.button(label="›", style=nextcord.ButtonStyle.secondary)
    async def next_button(
        self, _: nextcord.ui.Button, interaction: nextcord.Interaction
    ) -> None:
        total_pages = max(1, (len(self.tracks) + self.per_page - 1) // self.per_page)
        self.page = min(total_pages - 1, self.page + 1)
        self._update_buttons()
        await interaction.response.defer()
        await self.update_message()

    @nextcord.ui.button(label="≫", style=nextcord.ButtonStyle.secondary)
    async def last_button(
        self, _: nextcord.ui.Button, interaction: nextcord.Interaction
    ) -> None:
        total_pages = max(1, (len(self.tracks) + self.per_page - 1) // self.per_page)
        self.page = total_pages - 1
        self._update_buttons()
        await interaction.response.defer()
        await self.update_message()


@dataclass(slots=True)
class DiagnosticsReport:
    lavalink_latency_ms: Optional[float]
    lavalink_version: Optional[str]
    youtube_plugin_version: Optional[str]
    yt_dlp_version: str
    cookie_file_age_seconds: Optional[float]
    metrics: Dict[str, Any]


class DiagnosticsService:
    """Collect lightweight diagnostics from Lavalink and yt-dlp."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        password: str,
        secure: bool,
        cookies: CookieManager,
        metrics: PlaybackMetrics,
    ) -> None:
        self.host = host
        self.port = port
        self.password = password
        self.secure = secure
        self.cookies = cookies
        self.metrics = metrics

    async def collect(self) -> DiagnosticsReport:
        base_url = f"{'https' if self.secure else 'http'}://{self.host}:{self.port}"
        headers = {"Authorization": self.password}
        timeout = aiohttp.ClientTimeout(total=5)
        version_data: Dict[str, Any] = {}
        plugin_data: Dict[str, Any] = {}
        latency_ms: Optional[float] = None

        async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
            start = time.perf_counter()
            async with session.get(f"{base_url}/version") as resp:
                if resp.status == 200:
                    version_data = await resp.json()
            latency_ms = (time.perf_counter() - start) * 1000
            async with session.get(f"{base_url}/plugins") as resp:
                if resp.status == 200:
                    plugin_data = await resp.json()

        plugin_version = None
        plugins = (
            plugin_data
            if isinstance(plugin_data, list)
            else plugin_data.get("plugins", [])
        )
        for plugin in plugins:
            name = plugin.get("name") if isinstance(plugin, dict) else None
            if name == "dev.lavalink.youtube":
                plugin_version = plugin.get("version")
                break

        report = DiagnosticsReport(
            lavalink_latency_ms=(
                round(latency_ms, 2) if latency_ms is not None else None
            ),
            lavalink_version=version_data.get("version"),
            youtube_plugin_version=plugin_version,
            yt_dlp_version=yt_dlp.version.__version__,
            cookie_file_age_seconds=self.cookies.cookie_age_seconds(),
            metrics=self.metrics.snapshot(),
        )
        return report


class _JsonFormatter(logging.Formatter):
    def format(
        self, record: logging.LogRecord
    ) -> str:  # pragma: no cover - formatting only
        payload: Dict[str, Any] = {
            "level": record.levelname,
            "name": record.name,
            "message": record.getMessage(),
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%SZ"),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        if record.args and isinstance(record.args, dict):
            payload.update(record.args)
        extra = record.__dict__.get("extra")
        if isinstance(extra, dict):
            payload.update(extra)
        return json.dumps(payload, default=str)


def configure_json_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    logging.basicConfig(level=level, handlers=[handler])
