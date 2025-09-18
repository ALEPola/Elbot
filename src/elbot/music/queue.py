"""Thread-safe deque for music playback."""

from __future__ import annotations

import random
import threading
import uuid
from collections import deque
from dataclasses import dataclass, replace
from typing import Deque, Iterable, List, Optional

from .audio_backend import TrackHandle

__all__ = ["QueuedTrack", "MusicQueue"]


@dataclass(slots=True)
class QueuedTrack:
    """Metadata stored in the queue."""

    id: str
    handle: TrackHandle
    query: str
    channel_id: int
    requested_by: int
    requester_display: str
    is_fallback: bool = False
    fallback_source: Optional[str] = None

    def clone(self) -> "QueuedTrack":
        return replace(self, id=uuid.uuid4().hex)


class MusicQueue:
    """A thread-safe deque with atomic helpers."""

    def __init__(self) -> None:
        self._queue: Deque[QueuedTrack] = deque()
        self._lock = threading.Lock()
        self._last_played: Optional[QueuedTrack] = None

    # ------------------------------------------------------------------
    # Basic operations
    # ------------------------------------------------------------------
    def __len__(self) -> int:
        with self._lock:
            return len(self._queue)

    def snapshot(self) -> List[QueuedTrack]:
        with self._lock:
            return list(self._queue)

    def add(self, track: QueuedTrack) -> None:
        with self._lock:
            self._queue.append(track)

    def add_next(self, track: QueuedTrack) -> None:
        with self._lock:
            self._queue.appendleft(track)

    def pop_next(self) -> Optional[QueuedTrack]:
        with self._lock:
            if not self._queue:
                return None
            track = self._queue.popleft()
            self._last_played = track
            return track

    def peek(self, index: int = 0) -> Optional[QueuedTrack]:
        with self._lock:
            if index < 0 or index >= len(self._queue):
                return None
            return list(self._queue)[index]

    def clear(self) -> None:
        with self._lock:
            self._queue.clear()

    # ------------------------------------------------------------------
    # Advanced operations
    # ------------------------------------------------------------------
    def remove_index(self, index: int) -> Optional[QueuedTrack]:
        with self._lock:
            if index < 0 or index >= len(self._queue):
                return None
            items = list(self._queue)
            track = items.pop(index)
            self._queue = deque(items)
            return track

    def remove_range(self, start: int, end: int) -> List[QueuedTrack]:
        with self._lock:
            if start < 0:
                start = 0
            items = list(self._queue)
            end = min(end, len(items) - 1)
            removed: List[QueuedTrack] = []
            if start > end:
                return removed
            for idx in range(end, start - 1, -1):
                removed.append(items.pop(idx))
            self._queue = deque(items)
            removed.reverse()
            return removed

    def move(self, source_index: int, dest_index: int) -> bool:
        with self._lock:
            items = list(self._queue)
            if source_index < 0 or source_index >= len(items):
                return False
            dest_index = max(0, min(dest_index, len(items) - 1))
            track = items.pop(source_index)
            items.insert(dest_index, track)
            self._queue = deque(items)
            return True

    def shuffle(self) -> None:
        with self._lock:
            items = list(self._queue)
            random.shuffle(items)
            self._queue = deque(items)

    def replay_last(self) -> Optional[QueuedTrack]:
        with self._lock:
            if not self._last_played:
                return None
            replay_track = self._last_played.clone()
            self._queue.appendleft(replay_track)
            return replay_track

    # ------------------------------------------------------------------
    # Iterable support
    # ------------------------------------------------------------------
    def __iter__(self) -> Iterable[QueuedTrack]:
        return iter(self.snapshot())

