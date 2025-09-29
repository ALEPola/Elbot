"""Cookie management for YouTube integrations."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Dict, Optional

from platformdirs import user_cache_dir

__all__ = ["CookieManager"]

_logger = logging.getLogger("elbot.music.cookies")
_DEFAULT_EXPORT_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
_AUTO_ENV_TRUE = {"1", "true", "yes", "on"}


class CookieManager:
    """Monitor and lazily reload YouTube cookies, with optional auto-export."""

    def __init__(
        self,
        *,
        env_var: str = "YT_COOKIES_FILE",
        auto_export: Optional[bool] = None,
        browser: Optional[str] = None,
        browser_profile: Optional[str] = None,
        max_age_seconds: Optional[float] = None,
    ) -> None:
        self.env_var = env_var
        self._alt_env_vars = ("YTDLP_COOKIES_FILE", "YTDLP_COOKIES_PATH")
        self._lock = threading.Lock()
        self._path: Optional[Path] = None
        self._mtime: Optional[float] = None
        self._last_check: float = 0.0

        auto_env = os.getenv("YTDLP_AUTO_EXPORT")
        self._auto_export_enabled = (
            auto_export if auto_export is not None else str(auto_env or "").lower() in _AUTO_ENV_TRUE
        )
        self._auto_browser = browser or os.getenv("YTDLP_BROWSER")
        profile_env = browser_profile or os.getenv("YTDLP_BROWSER_PROFILE")
        self._auto_browser_profile = profile_env.strip() if profile_env else None
        self._export_interval = float(os.getenv("YTDLP_COOKIES_EXPORT_INTERVAL", "60"))
        timeout_env = os.getenv("YTDLP_COOKIES_EXPORT_TIMEOUT", "30")
        try:
            self._export_timeout = float(timeout_env)
        except ValueError:
            self._export_timeout = 30.0
        max_age_env = max_age_seconds if max_age_seconds is not None else os.getenv("YTDLP_COOKIES_MAX_AGE")
        self._auto_max_age: Optional[float]
        if isinstance(max_age_env, (int, float)):
            self._auto_max_age = float(max_age_env)
        elif isinstance(max_age_env, str) and max_age_env:
            try:
                self._auto_max_age = float(max_age_env)
            except ValueError:
                self._auto_max_age = None
        else:
            self._auto_max_age = None

        self._default_cookie_path = self._determine_default_cookie_path()
        self._last_export_attempt: float = 0.0\n        self._missing_browser_warned = False\n
        self._load_from_env()
        if self._path is None and self._auto_export_enabled:
            self._path = self._default_cookie_path

    # ------------------------------------------------------------------
    def _determine_default_cookie_path(self) -> Path:
        configured = os.getenv("YTDLP_COOKIES_OUTPUT")
        if configured:
            return Path(configured).expanduser().resolve()
        alt = os.getenv("ELBOT_DATA_DIR")
        if alt:
            return Path(alt).expanduser().resolve() / "yt-cookies.txt"
        cache_dir = Path(user_cache_dir("Elbot", "ElbotTeam"))
        return cache_dir / "yt-cookies.txt"

    def _load_from_env(self) -> None:
        for candidate in (self.env_var, *self._alt_env_vars):
            value = os.getenv(candidate)
            if not value:
                continue
            resolved = Path(value).expanduser().resolve()
            self._path = resolved
            self._mtime = resolved.stat().st_mtime if resolved.exists() else None
            return
        self._path = None
        self._mtime = None

    def _refresh_if_needed(self) -> None:
        with self._lock:
            now = time.monotonic()
            if now - self._last_check < 1.0:
                return
            self._last_check = now

            for candidate in (self.env_var, *self._alt_env_vars):
                path_from_env = os.getenv(candidate)
                if not path_from_env:
                    continue
                resolved = Path(path_from_env).expanduser().resolve()
                if self._path is None or resolved != self._path:
                    self._path = resolved
                    self._mtime = resolved.stat().st_mtime if resolved.exists() else None
                break
            else:
                if self._path and self._auto_export_enabled:
                    # path managed by auto-export; keep monitoring
                    pass
                elif not self._auto_export_enabled:
                    self._path = None
                    self._mtime = None

            if self._auto_export_enabled:
                target = self._path or self._default_cookie_path
                self._maybe_auto_export(target)

            if self._path is None:
                return
            if self._path.exists():
                mtime = self._path.stat().st_mtime
                if self._mtime != mtime:
                    self._mtime = mtime
            else:
                self._mtime = None

    def _maybe_auto_export(self, target: Path) -> None:
        if not self._auto_browser:
            if not self._missing_browser_warned:
                _logger.warning(
                    "YTDLP_AUTO_EXPORT enabled but YTDLP_BROWSER is unset; skipping auto-export.")
                self._missing_browser_warned = True
            return
        needs_export = not target.exists()
        if not needs_export and self._auto_max_age is not None and target.exists():
            age = time.time() - target.stat().st_mtime
            if age >= self._auto_max_age:
                needs_export = True
        if not needs_export:
            return

        now = time.monotonic()
        if now - self._last_export_attempt < self._export_interval:
            return
        self._last_export_attempt = now

        target.parent.mkdir(parents=True, exist_ok=True)
        if self._export_cookies(target):
            if target.exists():
                self._path = target
                self._mtime = target.stat().st_mtime

    def _export_cookies(self, target: Path) -> bool:
        browser_spec = self._auto_browser
        if self._auto_browser_profile:
            browser_spec = f"{browser_spec}::{self._auto_browser_profile}"
        cmd = [
            sys.executable,
            "-m",
            "yt_dlp",
            "--cookies-from-browser",
            browser_spec,
            "--cookies",
            str(target),
            "--no-download",
            os.getenv("YTDLP_COOKIES_TEST_URL", _DEFAULT_EXPORT_URL),
        ]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                timeout=self._export_timeout,
            )
        except FileNotFoundError:
            _logger.warning(
                "yt-dlp executable not found; disable YTDLP_AUTO_EXPORT or ensure yt-dlp is installed."
            )
            return False
        except subprocess.CalledProcessError as exc:
            snippet = (exc.stderr or exc.stdout or "").strip()
            _logger.warning(
                "yt-dlp cookie export failed (code %s): %s",
                exc.returncode,
                snippet.splitlines()[0] if snippet else "<no output>",
            )
            return False
        except Exception as exc:  # pragma: no cover - defensive
            _logger.warning("Unexpected error exporting cookies: %s", exc)
            return False

        stdout = (result.stdout or "").strip().splitlines()
        extracted_line = next((line for line in stdout if "Extracted" in line), None)
        if extracted_line:
            _logger.info("Auto-exported YouTube cookies via %s -> %s (%s)", browser_spec, target, extracted_line)
        else:
            _logger.info("Auto-exported YouTube cookies via %s -> %s", browser_spec, target)
        return True

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

