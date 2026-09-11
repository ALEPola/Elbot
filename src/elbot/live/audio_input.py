"""Bounded, in-memory speaker audio, independent of the music transport.

Only an authenticated Discord transport may bind streams. Spoken names or
model output must never call ``bind``. PCM is signed 16-bit little-endian,
48 kHz stereo (Discord's decoded format). This module does not transcribe,
write audio, or send it to a service.
"""

from collections import deque
from dataclasses import dataclass, field
import threading
import time
from typing import Callable


@dataclass(frozen=True)
class Speaker:
    user_id: int
    display_name: str
    guild_id: int
    channel_id: int
    is_bot: bool = False


@dataclass(frozen=True)
class SpeakerEvent:
    kind: str
    speaker: Speaker


@dataclass
class _Stream:
    speaker: Speaker
    frames: deque = field(default_factory=deque)
    size: int = 0
    last_audio: float = 0.0
    speaking: bool = False
    sequence: int | None = None


class SpeakerAudio:
    """One receive session with authenticated stream-to-member bindings.

    A session generation rejects callbacks from old connections. Unknown
    streams and bot accounts are dropped before buffering. ``tick`` must run
    periodically even when no one speaks to expire retained audio and emit
    activity-stop events. Activity indicates received nonzero PCM, not a VAD
    or a wake-word decision. A transport without RTP sequence numbers should
    omit ``sequence``; it must not fabricate packet-loss measurements.
    """

    BYTES_PER_SECOND = 48_000 * 2 * 2
    MAX_FRAME_BYTES = BYTES_PER_SECOND * 120 // 1000

    def __init__(
        self, guild_id: int, channel_id: int, *, buffer_seconds: float = 1.0,
        silence_seconds: float = 0.5, max_speakers: int = 32,
        clock: Callable[[], float] = time.monotonic,
    ):
        if not 0 < buffer_seconds <= 2 or not 0 < silence_seconds <= 5 or not 1 <= max_speakers <= 128:
            raise ValueError("Invalid receive limits")
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.buffer_seconds = buffer_seconds
        self.silence_seconds = silence_seconds
        self.max_speakers = max_speakers
        self.max_bytes = int(buffer_seconds * self.BYTES_PER_SECOND) // 4 * 4
        self.clock = clock
        self.generation = 0
        self.active = False
        self._streams: dict[int, _Stream] = {}
        self._events: deque[SpeakerEvent] = deque(maxlen=256)
        self._lock = threading.RLock()
        self.metrics = {key: 0 for key in (
            "frames", "dropped", "sequence_gaps", "late_or_duplicate", "expired_bytes",
        )}

    def start(self) -> int:
        with self._lock:
            self.stop()
            self.generation += 1
            self.active = True
            return self.generation

    def stop(self) -> None:
        with self._lock:
            for stream in self._streams.values():
                self._clear(stream)
            self._streams.clear()
            self.active = False

    def bind(self, generation: int, ssrc: int, speaker: Speaker) -> bool:
        with self._lock:
            if (not self.active or generation != self.generation or speaker.is_bot
                    or speaker.guild_id != self.guild_id or speaker.channel_id != self.channel_id
                    or speaker.user_id <= 0 or not 0 <= ssrc <= 0xFFFFFFFF):
                return False
            old = self._streams.get(ssrc)
            if old and old.speaker.user_id == speaker.user_id:
                old.speaker = speaker  # Discord nickname changes are authoritative.
                return True
            if old:
                self._clear(old)
                del self._streams[ssrc]
            # Stream replacement invalidates the old SSRC and its buffered audio.
            self.remove(speaker.user_id)
            if len(self._streams) >= self.max_speakers:
                return False
            self._streams[ssrc] = _Stream(speaker)
            return True

    def remove(self, user_id: int) -> None:
        with self._lock:
            for ssrc, stream in list(self._streams.items()):
                if stream.speaker.user_id == user_id:
                    self._clear(stream)
                    del self._streams[ssrc]

    def feed(self, generation: int, ssrc: int, pcm: bytes, *, sequence: int | None = None) -> bool:
        with self._lock:
            stream = self._streams.get(ssrc)
            if (not self.active or generation != self.generation or stream is None
                    or not isinstance(pcm, bytes) or not pcm or len(pcm) % 4
                    or len(pcm) > self.MAX_FRAME_BYTES):
                self.metrics["dropped"] += 1
                return False
            if sequence is not None:
                if not 0 <= sequence <= 65535:
                    self.metrics["dropped"] += 1
                    return False
                if stream.sequence is not None:
                    delta = (sequence - stream.sequence) & 0xFFFF
                    if delta == 0 or delta >= 32768:
                        self.metrics["late_or_duplicate"] += 1
                        return False
                    self.metrics["sequence_gaps"] += delta - 1
                stream.sequence = sequence
            now = self.clock()
            self._expire(stream, now)
            self.metrics["frames"] += 1
            if any(pcm):
                stream.last_audio = now
                if not stream.speaking:
                    stream.speaking = True
                    self._events.append(SpeakerEvent("speaker_start", stream.speaker))
            frame = bytearray(pcm)
            stream.frames.append((now, frame))
            stream.size += len(frame)
            while stream.size > self.max_bytes:
                self._drop_frame(stream)
            return True

    def tick(self) -> list[SpeakerEvent]:
        with self._lock:
            now = self.clock()
            for stream in self._streams.values():
                self._expire(stream, now)
            events = list(self._events)
            self._events.clear()
            return events

    def snapshot(self, user_id: int) -> bytes:
        """Copy a speaker's current window. The caller owns prompt disposal."""
        with self._lock:
            for stream in self._streams.values():
                if stream.speaker.user_id == user_id:
                    self._expire(stream, self.clock())
                    return b"".join(frame for _, frame in stream.frames)
            return b""

    def _drop_frame(self, stream: _Stream) -> None:
        _, frame = stream.frames.popleft()
        stream.size -= len(frame)
        self.metrics["expired_bytes"] += len(frame)
        frame[:] = b"\0" * len(frame)

    def _clear(self, stream: _Stream) -> None:
        while stream.frames:
            self._drop_frame(stream)
        if stream.speaking:
            stream.speaking = False
            self._events.append(SpeakerEvent("speaker_stop", stream.speaker))

    def _expire(self, stream: _Stream, now: float) -> None:
        while stream.frames and now - stream.frames[0][0] >= self.buffer_seconds:
            self._drop_frame(stream)
        if stream.speaking and now - stream.last_audio >= self.silence_seconds:
            stream.speaking = False
            self._events.append(SpeakerEvent("speaker_stop", stream.speaker))
