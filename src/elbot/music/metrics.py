"""Thread-safe counters for music playback metrics."""

from __future__ import annotations

import threading
from collections import Counter
from dataclasses import dataclass
from typing import Dict, Optional

__all__ = ["PlaybackMetrics"]


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

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------
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

