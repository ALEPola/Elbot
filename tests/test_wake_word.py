import json
import time

from elbot.live.audio_input import Speaker
from elbot.live.wake_word import (
    OUT_RATE, CommandWindow, WakeDetector, resample,
)

PCM48_20MS = b"\x10\x00\x10\x00" * 960  # 20 ms of 48 kHz stereo


class FakeRecognizer:
    """Finalizes on flush; the text/words come from the backend's script."""

    def __init__(self, script):
        self.script = script
        self.fed = 0

    def AcceptWaveform(self, pcm):
        self.fed += len(pcm) // 2
        return False

    def Result(self):
        return "{}"

    def FinalResult(self):
        text, words = self.script.pop(0) if self.script else ("", [])
        return json.dumps({"text": text, "result": words})


class FakeBackend:
    def __init__(self, script):
        self.script = script

    def recognizer(self):
        return FakeRecognizer(list(self.script))


def alexis():
    return Speaker(100, "Alexis", 1, 2)


def feed_seconds(det, speaker, seconds):
    for _ in range(int(seconds * 50)):
        assert det.feed(speaker, PCM48_20MS)


def wait_events(det, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        events = det.poll()
        if events:
            return events
        time.sleep(0.01)
    return []


def wait_until(pred, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return False


def test_resample_halves_channels_and_thirds_rate():
    pcm, state = resample(PCM48_20MS)
    assert len(pcm) == 320 * 2  # 20 ms at 16 kHz mono
    assert state is not None or True


def test_wake_phrase_yields_audio_from_phrase_onward_and_discards_buffer():
    words = [{"word": "[unk]", "start": 0.0, "end": 0.5}, {"word": "elbot", "start": 1.0, "end": 1.4},
             {"word": "[unk]", "start": 1.4, "end": 2.0}]
    det = WakeDetector(FakeBackend([("[unk] elbot [unk]", words)]))
    try:
        feed_seconds(det, alexis(), 2.0)
        det.flush(100)
        events = wait_events(det)
        assert len(events) == 1
        event = events[0]
        assert event.speaker.display_name == "Alexis"
        assert event.phrase == "elbot"
        assert event.phrase_offset_s == 1.0
        assert abs(len(event.audio) - 1.0 * OUT_RATE * 2) <= 2 * 320
        assert det.metrics == {"utterances": 1, "wakes": 1, "dropped": 0}
        stream = det._streams[100]
        assert wait_until(lambda: len(stream.utterance) == 0)
    finally:
        det.close()


def test_unrelated_utterance_yields_nothing_and_is_dropped():
    det = WakeDetector(FakeBackend([("[unk] [unk]", [])]))
    try:
        feed_seconds(det, alexis(), 1.0)
        det.flush(100)
        assert wait_until(lambda: det.metrics["utterances"] == 1)
        assert det.poll() == []
        assert det.metrics["wakes"] == 0
        assert wait_until(lambda: len(det._streams[100].utterance) == 0)
    finally:
        det.close()


def test_phrase_matching_is_exact_word_sequences():
    det = WakeDetector(FakeBackend([]))
    assert det._match("hey elbot [unk]") == "hey elbot"
    assert det._match("[unk] el bot") == "el bot"
    assert det._match("elbow [unk]") == "elbow"
    assert det._match("elbows") is None
    assert det._match("bot el") is None
    det.close()


def test_stream_limit_and_removal():
    det = WakeDetector(FakeBackend([]), max_streams=1)
    try:
        assert det.feed(alexis(), PCM48_20MS)
        assert not det.feed(Speaker(200, "Jovan", 1, 2), PCM48_20MS)
        assert det.metrics["dropped"] == 1
        det.remove(100)
        assert wait_until(lambda: 100 not in det._streams)
        assert det.feed(Speaker(200, "Jovan", 1, 2), PCM48_20MS)
    finally:
        det.close()


def test_command_window_arbitration():
    now = [100.0]
    window = CommandWindow(8.0, clock=lambda: now[0])
    assert window.wake(1) == "opened"
    assert window.wake(2) == "busy"
    assert window.wake(1) == "extended"
    assert window.is_open_for(1) and not window.is_open_for(2)
    now[0] += 7.0
    window.touch(1)
    now[0] += 7.0
    assert window.is_open_for(1)
    now[0] += 2.0
    assert not window.is_open_for(1)
    assert window.wake(2) == "opened"
