"""Small per-user persistent memory for GPT-Live.

Stores short free-text notes GPT-Live is told to remember about a specific
Discord user (a preferred name, a music taste, a running joke), keyed by
Discord user id and persisted as JSON across restarts and sessions. This is
deliberately tiny - a capped list of short strings per user, not a general
knowledge base - and global across guilds, since a Discord user id means
the same person everywhere.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("elbot.live.memory")

MAX_NOTES_PER_USER = 20
MAX_NOTE_LENGTH = 200


class UserMemory:
    def __init__(self, path: Optional[Path]):
        self.path = path
        self._lock = threading.Lock()
        self._data: dict[str, list[dict]] = {}
        if path is None or not path.exists():
            return
        try:
            loaded = json.loads(path.read_text())
        except (OSError, ValueError):
            return
        if not isinstance(loaded, dict):
            return
        for user_id, notes in loaded.items():
            if not isinstance(notes, list):
                continue
            cleaned = [
                {"text": str(n["text"])[:MAX_NOTE_LENGTH], "at": float(n.get("at", 0.0))}
                for n in notes
                if isinstance(n, dict) and n.get("text")
            ]
            if cleaned:
                self._data[str(user_id)] = cleaned[-MAX_NOTES_PER_USER:]

    def remember(self, user_id: int, text: str) -> None:
        text = text.strip()[:MAX_NOTE_LENGTH]
        if not text:
            return
        with self._lock:
            notes = self._data.setdefault(str(user_id), [])
            notes.append({"text": text, "at": time.time()})
            del notes[:-MAX_NOTES_PER_USER]
            self._persist()

    def recall(self, user_id: int) -> list[str]:
        with self._lock:
            return [n["text"] for n in self._data.get(str(user_id), [])]

    def forget_all(self, user_id: int) -> bool:
        with self._lock:
            existed = self._data.pop(str(user_id), None) is not None
            if existed:
                self._persist()
            return existed

    def _persist(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._data))
        except OSError:
            logger.warning("Could not persist GPT-Live user memory")
