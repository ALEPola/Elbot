import asyncio
import base64
from datetime import date
from types import SimpleNamespace

import pytest

from elbot.live.audio_input import Speaker
from elbot.live.controller import BARGE_IN_BYTES, OUT_FRAME, LiveController, upsample_to_discord
from elbot.live.session import LiveConfig, LiveSession, UsageLedger
from elbot.live.wake_word import CommandWindow, WakeEvent


def config(**overrides):
    base = dict(api_key="k", price_per_minute=0.06, session_max_s=600, warn_at_s=420,
                idle_s=0.3, daily_usd=1.0, monthly_usd=5.0)
    base.update(overrides)
    return LiveConfig(**base)


def test_ledger_tracks_days_months_and_allowance():
    ledger = UsageLedger(None)
    today = date(2026, 9, 10)
    ledger.add(300, today)  # 5 min = $0.30 at $0.06/min
    ledger.add(60, date(2026, 9, 3))
    assert ledger.seconds(today) == (300, 360)
    day_usd, month_usd = ledger.usd(0.06, today)
    assert round(day_usd, 2) == 0.30 and round(month_usd, 2) == 0.36
    cfg = config()
    assert round(ledger.allowance_s(cfg, today)) == 700  # $0.70 left today at $0.06/min
    ledger.add(700, today)
    assert ledger.allowance_s(cfg, today) == 0


@pytest.mark.asyncio
async def test_session_event_handling_without_a_socket():
    received = []

    async def on_audio(pcm):
        received.append(pcm)

    session = LiveSession(config(), on_audio=on_audio)
    await session._handle({"type": "session.started", "session": {"id": "sess_1"}})
    assert session.session_id == "sess_1" and session._started.is_set()
    await session._handle({"type": "session.output_audio.delta", "delta": base64.b64encode(b"\x01\x02").decode()})
    assert received == [b"\x01\x02"]
    await session._handle({"type": "response.event", "event": {"response": {"usage": {"input_tokens": 5, "output_tokens": 7}}}})
    await session._handle({"type": "session.closed", "reason": "close_requested", "usage": {"seconds": 12}})
    assert session.usage.reported_s == 12 and session.usage.close_reason == "close_requested"
    assert (session.usage.input_tokens, session.usage.output_tokens) == (5, 7)
    assert session._closed.is_set()


def test_upsample_16k_mono_to_48k_stereo():
    pcm, _ = upsample_to_discord(b"\x00\x10" * 160)  # 10 ms at 16 kHz
    assert abs(len(pcm) - 160 * 3 * 2 * 2) <= 16  # resampler filter delay may trim a few samples
    assert len(pcm) % 4 == 0


class FakeSession:
    def __init__(self, cfg, *, on_audio, on_event=None):
        self.on_audio = on_audio
        self.connected = False
        self.audio = []
        self.instructions = []
        self.session_id = "fake"
        self.usage = SimpleNamespace(close_reason="", reported_s=None, connected_s=0.0, input_tokens=0, output_tokens=0)
        self._t0 = None

    async def connect(self, timeout=15.0):
        self.connected = True

    def connected_seconds(self):
        return 1.0

    async def append_audio(self, pcm):
        self.audio.append(pcm)

    async def append_instructions(self, content):
        self.instructions.append(content)

    async def close(self, *, reason="requested", timeout=5.0):
        self.connected = False
        self.usage.close_reason = reason
        self.usage.reported_s = 9.0
        return self.usage


class FakePlayer:
    def __init__(self):
        self.guild = SimpleNamespace(id=1)
        self.window = CommandWindow(5.0)
        self.audio = SimpleNamespace(active=True)
        self.spoken = []
        self.cleared = 0
        self.live_tap = None
        self._connected = True

    def is_connected(self):
        return self._connected

    async def speak(self, pcm):
        self.spoken.append(pcm)

    async def speak_clear(self):
        self.cleared += 1


def wake(uid=100, name="Alexis", audio=b"\x01\x00" * 1600):
    return WakeEvent(Speaker(uid, name, 1, 2), "elbot [unk]", "elbot", 0.0, 0.0, audio)


def make(cfg=None, ledger=None):
    player = FakePlayer()
    announced = []

    async def announce(text):
        announced.append(text)

    ledger = ledger or UsageLedger(None)
    ctrl = LiveController(player, cfg or config(), ledger, announce=announce, session_factory=FakeSession)
    return ctrl, player, announced, ledger


@pytest.mark.asyncio
async def test_wake_connects_sends_context_and_only_active_speaker_audio():
    ctrl, player, announced, ledger = make(config(idle_s=60))
    await ctrl.start()
    try:
        assert player.live_tap is ctrl._tap_fn
        player.window.wake(100)
        await ctrl.on_wake(wake(), "opened")
        session = ctrl.session
        assert session.connected and ctrl.stats["sessions"] == 1
        assert "Alexis" in session.instructions[0] and "100" in session.instructions[0]
        assert session.audio[0] == b"\x01\x00" * 1600

        player.live_tap(Speaker(200, "Jovan", 1, 2), b"\x05\x00\x05\x00" * 960)
        player.live_tap(Speaker(100, "Alexis", 1, 2), b"\x05\x00\x05\x00" * 960)
        await asyncio.sleep(0.35)
        assert len(session.audio) >= 2
        chunk = session.audio[1]
        assert any(chunk[:640]) and not any(chunk[700:])  # Alexis's 20 ms, then silence padding; Jovan's dropped

        await ctrl._on_audio(b"\x00\x10" * 3200)  # 200 ms of bot speech (above the silence gate)
        await asyncio.sleep(0.2)
        assert player.spoken and sum(len(s) for s in player.spoken) % OUT_FRAME == 0
    finally:
        await ctrl.stop("test")
    assert not ctrl.running and player.live_tap is None
    assert ledger.seconds()[0] == 9.0 and ctrl.stats["usd"] > 0


@pytest.mark.asyncio
async def test_barge_in_clears_pending_bot_speech():
    ctrl, player, _, _ = make(config(idle_s=60))
    await ctrl.start()
    try:
        player.window.wake(100)
        await ctrl.on_wake(wake(), "opened")
        ctrl._out_buf += b"\0" * (BARGE_IN_BYTES + OUT_FRAME)
        for _ in range(5):  # a syllable is not a barge-in
            player.live_tap(Speaker(100, "Alexis", 1, 2), b"\x01\x00\x01\x00" * 960)
        assert ctrl.stats["barge_ins"] == 0 and len(ctrl._out_buf) > 0
        for _ in range(12):  # sustained speech is
            player.live_tap(Speaker(100, "Alexis", 1, 2), b"\x01\x00\x01\x00" * 960)
        await asyncio.sleep(0.05)
        assert ctrl.stats["barge_ins"] == 1 and len(ctrl._out_buf) == 0 and player.cleared >= 1
    finally:
        await ctrl.stop("test")


@pytest.mark.asyncio
async def test_spend_cap_blocks_connection_and_idle_closes_session():
    ledger = UsageLedger(None)
    ledger.add(10_000)  # far over the $1/day cap
    ctrl, player, announced, _ = make(config(), ledger)
    await ctrl.start()
    try:
        player.window.wake(100)
        await ctrl.on_wake(wake(), "opened")
        assert ctrl.session is None and "spend cap" in announced[-1]
    finally:
        await ctrl.stop("test")

    ctrl, player, announced, ledger = make(config(idle_s=0.3))
    await ctrl.start()
    try:
        player.window.wake(100)
        await ctrl.on_wake(wake(), "opened")
        assert ctrl.session is not None
        await asyncio.sleep(0.9)
        assert ctrl.session is None  # idle-closed, but still armed
        assert ctrl.running and ledger.seconds()[0] == 9.0
    finally:
        await ctrl.stop("test")


@pytest.mark.asyncio
async def test_listener_ending_stops_controller():
    stopped = []

    async def on_stopped(controller, reason):
        stopped.append(reason)

    player = FakePlayer()
    ctrl = LiveController(player, config(), UsageLedger(None),
                          announce=lambda t: asyncio.sleep(0), on_stopped=on_stopped, session_factory=FakeSession)
    await ctrl.start()
    player.audio.active = False
    await asyncio.sleep(0.6)
    assert stopped == ["listener ended"] and not ctrl.running


@pytest.mark.asyncio
async def test_silence_is_padded_in_real_time_while_window_open():
    ctrl, player, _, _ = make(config(idle_s=60))
    await ctrl.start()
    try:
        player.window.wake(100)
        await ctrl.on_wake(wake(audio=b""), "opened")
        await asyncio.sleep(0.6)
        sent = sum(len(a) for a in ctrl.session.audio) // 2
        assert 0.4 * 16000 <= sent <= 0.9 * 16000  # ~0.6 s of stream, all padding
        assert all(set(a) == {0} for a in ctrl.session.audio if a)
    finally:
        await ctrl.stop("test")


@pytest.mark.asyncio
async def test_output_silence_does_not_duck_or_keep_session_busy():
    ctrl, player, _, _ = make(config(idle_s=60))
    await ctrl.start()
    try:
        player.window.wake(100)
        await ctrl.on_wake(wake(), "opened")
        before = ctrl._last_activity
        await asyncio.sleep(0.01)
        await ctrl._on_audio(b"\x05\x00" * 1600)  # near-silent
        assert len(ctrl._out_buf) == 0 and ctrl._last_activity == before
        ctrl._last_activity = 0.0
        await ctrl._on_audio(b"\x00\x10" * 1600)  # speech-level
        assert len(ctrl._out_buf) > 0 and ctrl._last_activity > 0.0
    finally:
        await ctrl.stop("test")


@pytest.mark.asyncio
async def test_odd_length_audio_deltas_are_realigned_not_corrupted():
    ctrl, player, _, _ = make(config(idle_s=60))
    await ctrl.start()
    try:
        player.window.wake(100)
        await ctrl.on_wake(wake(), "opened")
        # GPT-Live's docs: delta chunk boundaries are arbitrary, so a delta can
        # split a 16-bit sample. Feed 3 odd-length chunks that only add up to a
        # whole number of samples together.
        await ctrl._on_audio(b"\x00\x10\x00")       # 1.5 samples
        await ctrl._on_audio(b"\x10\x00\x10\x00\x00")  # + 2.5 samples
        await ctrl._on_audio(b"\x10")                # + 0.5 -> 4.5 total, 1 byte held back
        total = 3 + 5 + 1
        assert (total - len(ctrl._out_pending)) % 2 == 0
        await asyncio.sleep(0.1)
        assert len(player.spoken) == 0 or all(len(s) % 4 == 0 for s in player.spoken)
    finally:
        await ctrl.stop("test")
