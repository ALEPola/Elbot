"""Environment-backed configuration helpers."""

from __future__ import annotations

import logging
import os
import socket
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable, List

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[2]
DYNAMIC_PORT_START = 2333
DYNAMIC_PORT_ATTEMPTS = 40
_DYNAMIC_PORT_SENTINELS = {"", "0", "auto", "dynamic", "random"}
# Only auto-load the .env file when not running under pytest to let tests
# control environment via monkeypatch. Detect pytest either via the
# PYTEST_CURRENT_TEST env var or by inspecting sys.argv for 'pytest'.
_running_under_pytest = bool(os.getenv("PYTEST_CURRENT_TEST")) or any(
    "pytest" in (arg or "") for arg in sys.argv
)
if not _running_under_pytest:
    load_dotenv(BASE_DIR / ".env")

logger = logging.getLogger("elbot.config")
_gid_str = os.getenv("GUILD_ID")

def _select_dynamic_lavalink_port(start: int = DYNAMIC_PORT_START, attempts: int = DYNAMIC_PORT_ATTEMPTS) -> int:
    """Return a free TCP port on 127.0.0.1 for Lavalink."""

    last_port = start + attempts - 1
    for port in range(start, last_port + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError(f"No free TCP port available in {start}-{last_port}")

class Config:
    """Central configuration loaded from environment variables."""

    BASE_DIR = BASE_DIR
    DISCORD_TOKEN = os.getenv("DISCORD_TOKEN") or os.getenv("DISCORD_BOT_TOKEN", "")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4")
    ICS_URL = os.getenv("ICS_URL", "")

    _f1_channel_str = os.getenv("F1_CHANNEL_ID")
    if _f1_channel_str:
        try:
            F1_CHANNEL_ID = int(_f1_channel_str)
        except ValueError:
            logger.warning(
                "Invalid F1_CHANNEL_ID '%s' - expected integer", _f1_channel_str
            )
            F1_CHANNEL_ID = 0
    else:
        F1_CHANNEL_ID = 0

    PREFIX = os.getenv("COMMAND_PREFIX", "!")
    BOT_USERNAME = os.getenv("ELBOT_USERNAME", "Elbot")

    LAVALINK_HOST = os.getenv("LAVALINK_HOST", "localhost")
    LAVALINK_PASSWORD = os.getenv("LAVALINK_PASSWORD", "youshallnotpass")

    _port_raw = os.getenv("LAVALINK_PORT")
    if _port_raw:
        try:
            LAVALINK_PORT = int(_port_raw)
        except ValueError:
            logger.error("Invalid LAVALINK_PORT value; expected integer, got '%s'", _port_raw)
            raise SystemExit(1)
    else:
        LAVALINK_PORT = DEFAULT_LAVALINK_PORT

    YT_COOKIES_FILE = os.getenv("YT_COOKIES_FILE") or os.getenv("YTDLP_COOKIES_FILE")
    if not YT_COOKIES_FILE:
        YT_COOKIES_FILE = os.getenv("YTDLP_COOKIES_PATH")

    AUTO_LAVALINK = os.getenv("AUTO_LAVALINK", "0") == "1"

    if _gid_str:
        try:
            GUILD_ID = int(_gid_str)
        except ValueError:
            logger.warning("Invalid GUILD_ID '%s' - expected integer", _gid_str)
            GUILD_ID = None
    else:
        GUILD_ID = None

    @staticmethod
    def _missing_keys(keys: Iterable[str]) -> List[str]:
        return [key for key in keys if not os.getenv(key)]

    @staticmethod
    def validate() -> None:
        required = ["DISCORD_TOKEN", "LAVALINK_HOST", "LAVALINK_PASSWORD"]
        missing = Config._missing_keys(required)

        if missing:
            joined = ", ".join(missing)
            logger.error("configuration missing required keys: %s", joined)
            sys.exit(1)

        port_value = os.getenv("LAVALINK_PORT", str(Config.LAVALINK_PORT))
        try:
            int(port_value)
        except (TypeError, ValueError):
            logger.error("configuration invalid: LAVALINK_PORT must be an integer")
            sys.exit(1)


def get_lavalink_connection_info() -> tuple[str, int, str, bool]:
    """Return Lavalink connection info using runtime environment overrides."""

    host = os.getenv("LAVALINK_HOST") or Config.LAVALINK_HOST or "127.0.0.1"
    port = Config.LAVALINK_PORT
    port_raw = os.getenv("LAVALINK_PORT")
    if port_raw:
        try:
            port = int(port_raw)
        except ValueError:
            logger.warning(
                "Invalid runtime LAVALINK_PORT '%s'; falling back to %s",
                port_raw,
                port,
            )
    password = os.getenv("LAVALINK_PASSWORD") or Config.LAVALINK_PASSWORD
    secure = os.getenv("LAVALINK_SSL", "false").lower() == "true"
    return host, port, password, secure

def log_cookie_status() -> None:
    """Log the configured YouTube cookie file, if any."""

    path = Config.YT_COOKIES_FILE
    if not path:
        logger.info("cookies: none")
        return

    resolved = Path(path).expanduser()
    if not resolved.exists():
        logger.info("cookies: path=%s missing", resolved)
        return

    mtime = datetime.fromtimestamp(resolved.stat().st_mtime)
    logger.info("cookies: path=%s mtime=%s", resolved, mtime.isoformat())





