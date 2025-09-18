"""High level music playback utilities for Elbot."""

from .audio_backend import LavalinkAudioBackend
from .fallback import FallbackPlayer
from .queue import MusicQueue, QueuedTrack
from .embeds import EmbedFactory
from .diagnostics import DiagnosticsReport, DiagnosticsService

__all__ = [
    "LavalinkAudioBackend",
    "FallbackPlayer",
    "MusicQueue",
    "QueuedTrack",
    "EmbedFactory",
    "DiagnosticsReport",
    "DiagnosticsService",
]
