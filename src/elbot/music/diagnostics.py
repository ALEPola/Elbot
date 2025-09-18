"""Diagnostics helpers for the music subsystem."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import aiohttp
import yt_dlp

from .cookies import CookieManager
from .metrics import PlaybackMetrics

__all__ = ["DiagnosticsReport", "DiagnosticsService"]


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
        plugins = plugin_data if isinstance(plugin_data, list) else plugin_data.get("plugins", [])
        for plugin in plugins:
            name = plugin.get("name") if isinstance(plugin, dict) else None
            if name == "dev.lavalink.youtube":
                plugin_version = plugin.get("version")
                break

        report = DiagnosticsReport(
            lavalink_latency_ms=round(latency_ms, 2) if latency_ms is not None else None,
            lavalink_version=version_data.get("version"),
            youtube_plugin_version=plugin_version,
            yt_dlp_version=yt_dlp.version.__version__,
            cookie_file_age_seconds=self.cookies.cookie_age_seconds(),
            metrics=self.metrics.snapshot(),
        )
        return report

