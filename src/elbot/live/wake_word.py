"""Local wake-phrase detection on isolated speaker streams.

One keyword-restricted recognizer per speaker runs on its own thread. Audio
is held in memory only until the current utterance finalizes and is then
zeroed and dropped, unless the utterance contains a wake phrase, in which
case the slice from the phrase onward is handed to a WakeEvent. Nothing is
transcribed beyond the grammar tokens and nothing leaves the process.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .audio_input import Speaker

try:
    import audioop
except ImportError:  # Python 3.13 removed it; see _resample fallback.
    audioop = None

logger = logging.getLogger("elbot.live.wake")

DEFAULT_PHRASES = ("elbot", "el bot", "hey elbot", "hey el bot", "elbow")
IN_RATE = 48_000
OUT_RATE = 16_000
MAX_UTTERANCE_SECONDS = 15
IDLE_SECONDS = 30
_FLUSH = object()
_STOP = object()


@dataclass(frozen=True)
class WakeEvent:
    speaker: Speaker
    text: str
    phrase: str
    at: float
    phrase_offset_s: float
    audio: bytes
    """16 kHz mono s16le from the wake phrase to the end of the utterance. The
    consumer owns disposal."""


def resample(pcm48: bytes, state=None):
    """48 kHz stereo s16le -> 16 kHz mono s16le. Returns (pcm, state)."""
    if audioop is not None:
        mono = audioop.tomono(pcm48, 2, 0.5, 0.5)
        return audioop.ratecv(mono, 2, 1, IN_RATE, OUT_RATE, state)
    out = bytearray()
    for i in range(0, len(pcm48) // 4, 3):
        left = int.from_bytes(pcm48[4 * i:4 * i + 2], "little", signed=True)
        right = int.from_bytes(pcm48[4 * i + 2:4 * i + 4], "little", signed=True)
        out += ((left + right) // 2).to_bytes(2, "little", signed=True)
    return bytes(out), None


class VoskBackend:
    def __init__(self, model_path: str, phrases=DEFAULT_PHRASES):
        import vosk

        vosk.SetLogLevel(-1)
        self._vosk = vosk
        self._model = vosk.Model(model_path)
        self._grammar = json.dumps([*phrases, "[unk]"])

    def recognizer(self):
        rec = self._vosk.KaldiRecognizer(self._model, OUT_RATE, self._grammar)
        rec.SetWords(True)
        return rec


class _Stream:
    def __init__(self, speaker: Speaker, recognizer):
        self.speaker = speaker
        self.rec = recognizer
        self.queue: queue.Queue = queue.Queue()
        self.utterance = bytearray()
        self.resample_state = None
        self.fed_samples = 0
        self.utterance_start = 0
        self.last_audio = time.monotonic()
        self.thread: Optional[threading.Thread] = None


class WakeDetector:
    """Feed 48 kHz stereo PCM per speaker; poll WakeEvents from the loop thread."""

    def __init__(self, backend, phrases=DEFAULT_PHRASES, *, max_streams: int = 6,
                 clock: Callable[[], float] = time.monotonic):
        self._backend = backend
        self._phrases = sorted((p.lower() for p in phrases), key=len, reverse=True)
        self._max_streams = max_streams
        self._clock = clock
        self._streams: dict[int, _Stream] = {}
        self._events: deque[WakeEvent] = deque(maxlen=64)
        self._lock = threading.Lock()
        self._closed = False
        self.metrics = {"utterances": 0, "wakes": 0, "dropped": 0}

    def feed(self, speaker: Speaker, pcm48: bytes) -> bool:
        with self._lock:
            if self._closed:
                return False
            stream = self._streams.get(speaker.user_id)
            if stream is None:
                if len(self._streams) >= self._max_streams:
                    self.metrics["dropped"] += 1
                    return False
                stream = _Stream(speaker, self._backend.recognizer())
                stream.thread = threading.Thread(
                    target=self._run, args=(stream,), name=f"wake-{speaker.user_id}", daemon=True,
                )
                self._streams[speaker.user_id] = stream
                stream.thread.start()
            stream.speaker = speaker
            stream.last_audio = self._clock()
        stream.queue.put(pcm48)
        return True

    def flush(self, user_id: int) -> None:
        stream = self._streams.get(user_id)
        if stream is not None:
            stream.queue.put(_FLUSH)

    def remove(self, user_id: int) -> None:
        with self._lock:
            stream = self._streams.pop(user_id, None)
        if stream is not None:
            stream.queue.put(_STOP)

    def poll(self) -> list[WakeEvent]:
        now = self._clock()
        with self._lock:
            idle = [uid for uid, s in self._streams.items() if now - s.last_audio > IDLE_SECONDS]
        for uid in idle:
            self.remove(uid)
        with self._lock:
            events = list(self._events)
            self._events.clear()
        return events

    def close(self) -> None:
        with self._lock:
            self._closed = True
            streams = list(self._streams.values())
            self._streams.clear()
        for stream in streams:
            stream.queue.put(_STOP)
        for stream in streams:
            if stream.thread is not None:
                stream.thread.join(timeout=2)

    def _run(self, stream: _Stream) -> None:
        try:
            while True:
                item = stream.queue.get()
                if item is _STOP:
                    break
                if item is _FLUSH:
                    self._finalize(stream, stream.rec.FinalResult())
                    continue
                pcm, stream.resample_state = resample(item, stream.resample_state)
                stream.utterance += pcm
                stream.fed_samples += len(pcm) // 2
                limit = MAX_UTTERANCE_SECONDS * OUT_RATE * 2
                if len(stream.utterance) > limit:
                    excess = len(stream.utterance) - limit
                    stream.utterance[:excess] = b"\0" * excess
                    del stream.utterance[:excess]
                    stream.utterance_start += excess // 2
                if stream.rec.AcceptWaveform(pcm):
                    self._finalize(stream, stream.rec.Result())
        except Exception:
            logger.exception("Wake detector thread failed", extra={"user_id": stream.speaker.user_id})
        finally:
            self._discard(stream)

    def _finalize(self, stream: _Stream, raw: str) -> None:
        try:
            result = json.loads(raw) if raw else {}
        except ValueError:
            result = {}
        text = result.get("text", "")
        if text:
            self.metrics["utterances"] += 1
        phrase = self._match(text)
        if phrase:
            utt_len_s = len(stream.utterance) / 2 / OUT_RATE
            start = self._phrase_start(result, phrase)
            rel = start - stream.utterance_start / OUT_RATE
            if not 0 <= rel <= utt_len_s:
                rel = start if 0 <= start <= utt_len_s else 0.0
            offset = int(rel * OUT_RATE) * 2
            event = WakeEvent(
                speaker=stream.speaker, text=text, phrase=phrase, at=self._clock(),
                phrase_offset_s=round(rel, 2), audio=bytes(stream.utterance[offset:]),
            )
            with self._lock:
                self._events.append(event)
                self.metrics["wakes"] += 1
        self._discard(stream)

    def _match(self, text: str) -> Optional[str]:
        words = text.split()
        for phrase in self._phrases:
            target = phrase.split()
            for i in range(len(words) - len(target) + 1):
                if words[i:i + len(target)] == target:
                    return phrase
        return None

    @staticmethod
    def _phrase_start(result: dict, phrase: str) -> float:
        first = phrase.split()[0]
        for word in result.get("result", []):
            if word.get("word") == first:
                return float(word.get("start", 0.0))
        return 0.0

    @staticmethod
    def _discard(stream: _Stream) -> None:
        stream.utterance[:] = b"\0" * len(stream.utterance)
        stream.utterance_start = stream.fed_samples
        stream.utterance.clear()


class CommandWindow:
    """One active speaker per guild; others are told to wait."""

    def __init__(self, seconds: float = 8.0, *, clock: Callable[[], float] = time.monotonic):
        self.seconds = seconds
        self._clock = clock
        self.active: Optional[int] = None
        self.until = 0.0

    def wake(self, user_id: int) -> str:
        self.expire()
        if self.active is None:
            self.active, self.until = user_id, self._clock() + self.seconds
            return "opened"
        if self.active == user_id:
            self.until = self._clock() + self.seconds
            return "extended"
        return "busy"

    def touch(self, user_id: int) -> None:
        if self.active == user_id:
            self.until = self._clock() + self.seconds

    def is_open_for(self, user_id: int) -> bool:
        self.expire()
        return self.active == user_id

    def expire(self) -> bool:
        if self.active is not None and self._clock() >= self.until:
            self.active, self.until = None, 0.0
            return True
        return False

    def close(self) -> None:
        self.active, self.until = None, 0.0


def create_detector() -> Optional[WakeDetector]:
    """Build the Vosk detector from the environment, or None with the reason logged."""
    if os.getenv("ELBOT_WAKE_ENABLED", "1") != "1":
        logger.info("Wake detection disabled by ELBOT_WAKE_ENABLED")
        return None
    model = os.getenv("ELBOT_WAKE_MODEL", "")
    if not model or not Path(model).is_dir():
        logger.warning("Wake detection unavailable: ELBOT_WAKE_MODEL is not a directory")
        return None
    phrases = tuple(
        p.strip().lower() for p in os.getenv("ELBOT_WAKE_PHRASES", "").split(",") if p.strip()
    ) or DEFAULT_PHRASES
    try:
        backend = VoskBackend(model, phrases)
    except Exception as exc:
        logger.warning("Wake detection unavailable: %s: %s", type(exc).__name__, exc)
        return None
    return WakeDetector(backend, phrases)
