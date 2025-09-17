"""Cookie management for YouTube integrations."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Dict, Optional

__all__ = ["CookieManager"]


class CookieManager:
    """Monitor and lazily reload YouTube cookies."""

    def __init__(self, *, env_var: str = "YT_COOKIES_FILE") -> None:
        self.env_var = env_var
        self._lock = threading.Lock()
        self._path: Optional[Path] = None
        self._mtime: Optional[float] = None
        self._last_check: float = 0.0
        self._load_from_env()

    # ------------------------------------------------------------------
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
                    self._mtime = resolved.stat().st_mtime if resolved.exists() else None
            if self._path is None:
                return
            if self._path.exists():
                mtime = self._path.stat().st_mtime
                if self._mtime != mtime:
                    self._mtime = mtime
            else:
                # File disappeared
                self._mtime = None

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------
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

