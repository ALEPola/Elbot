"""Bot-owned heartbeat shared with the local panel; never contains credentials."""

import json
import math
import time
from pathlib import Path

from .file_io import atomic_write_text


def publish_health(path: Path, *, discord_ready: bool, music_ready: bool) -> None:
    atomic_write_text(path, json.dumps({
        "updated_at": time.time(),
        "discord_ready": discord_ready,
        "music_ready": music_ready,
    }))


def read_health(path: Path, *, now: float | None = None) -> dict:
    unavailable = {"status": "unknown", "discord": "unknown", "music": "unknown", "age_seconds": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        timestamp = float(data["updated_at"])
        if not math.isfinite(timestamp) or not all(
            isinstance(data[key], bool) for key in ("discord_ready", "music_ready")
        ):
            return unavailable
        age = (time.time() if now is None else now) - timestamp
        if age < 0 or age > 45:
            return {**unavailable, "status": "stale", "age_seconds": max(0, int(age))}
        discord = data["discord_ready"]
        music = data["music_ready"]
        return {
            "status": "ready" if discord and music else "degraded" if discord else "disconnected",
            "discord": "connected" if discord else "disconnected",
            "music": "available" if music else "unavailable",
            "age_seconds": int(age),
        }
    except (OSError, ValueError, TypeError, KeyError):
        return unavailable
