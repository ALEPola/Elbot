"""High level music playback utilities for Elbot."""

from .core import (
    FallbackPlayer,
    LavalinkAudioBackend,
    LavalinkUnavailable,
    MusicQueue,
    QueuedTrack,
    TrackHandle,
    TrackLoadFailure,
)
from .support import (
    CookieManager,
    DiagnosticsReport,
    SearchCache,
    DiagnosticsService,
    EmbedFactory,
    PlaybackMetrics,
    QueuePaginator,
    configure_json_logging,
)

__all__ = [
    "FallbackPlayer",
    "LavalinkAudioBackend",
    "LavalinkUnavailable",
    "MusicQueue",
    "QueuedTrack",
    "TrackHandle",
    "TrackLoadFailure",
    "CookieManager",
    "DiagnosticsReport",
    "SearchCache",
    "DiagnosticsService",
    "EmbedFactory",
    "PlaybackMetrics",
    "QueuePaginator",
    "configure_json_logging",
]
