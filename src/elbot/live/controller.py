"""Per-guild GPT-Live orchestration.

Armed by /live start. The socket is opened only when someone addresses ELBOT
and closed again after idle, so connected (billed) time tracks real use.
Only the active speaker's audio inside an open command window is forwarded.
"""

from __future__ import annotations

import asyncio
import json
from elbot.music.actions import Actor, current_actor
from .schema import validate_arguments
import logging
import time
from typing import Awaitable, Callable, Optional

from .memory import UserMemory
from .session import LiveConfig, LiveSession, UsageLedger
from .wake_word import OUT_RATE, resample

try:
    import audioop
except ImportError:
    audioop = None

logger = logging.getLogger("elbot.live.controller")

OUT_FRAME = 3840  # 20 ms of 48 kHz stereo s16le
PACE_S = 0.06
BARGE_IN_BYTES = OUT_FRAME * 10  # 200 ms of pending bot speech


class LimitReached(RuntimeError):
    pass


SILENCE_PEAK = 250  # of 32767


def _is_silence(pcm16: bytes) -> bool:
    if audioop is not None:
        return audioop.max(pcm16, 2) < SILENCE_PEAK
    return all(abs(int.from_bytes(pcm16[i:i + 2], "little", signed=True)) < SILENCE_PEAK
               for i in range(0, len(pcm16) - 1, 2))


def upsample_to_discord(pcm16k: bytes, state=None):
    """16 kHz mono -> 48 kHz stereo s16le."""
    if audioop is not None:
        mono48, state = audioop.ratecv(pcm16k, 2, 1, OUT_RATE, 48_000, state)
        return audioop.tostereo(mono48, 2, 1, 1), state
    out = bytearray()
    for i in range(0, len(pcm16k) - 1, 2):
        sample = pcm16k[i:i + 2]
        out += sample * 6
    return bytes(out), None


class LiveController:
    def __init__(
        self, player, config: LiveConfig, ledger: UsageLedger, *,
        announce: Callable[[str], Awaitable[None]],
        on_stopped: Optional[Callable[["LiveController", str], Awaitable[None]]] = None,
        tools: Optional[dict[str, Callable[[dict], Awaitable[dict]]]] = None,
        memory: Optional[UserMemory] = None,
        session_factory: Callable[..., LiveSession] = LiveSession,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.player = player
        self.config = config
        self.ledger = ledger
        self.announce = announce
        self.on_stopped = on_stopped
        self.tools = tools or {}
        self.memory = memory
        self._session_factory = session_factory
        self._clock = clock
        self.session: Optional[LiveSession] = None
        self.active_speaker: Optional[int] = None
        self.active_name = ""
        self._tool_scope = None
        self._tool_actor = None
        self._wake_lock = asyncio.Lock()
        self._in_buf = bytearray()
        self._in_state = None
        self._stream_started = 0.0
        self._sent_samples = 0
        self._out_buf = bytearray()
        self._out_state = None
        self._out_pending = b""  # a byte held back when a delta splits a 16-bit sample
        self._output_silence_samples = 0
        self._input_open = False
        self._last_input_speech = 0.0
        self._last_output_speech = 0.0
        self._reply_seen = False
        self._followup_until = None
        self._followup_blocked = False
        self._playback_until = 0.0
        self._last_activity = 0.0
        self._warned = False
        self._allowance_s = 0.0
        self._accounted_s = 0.0
        self._tasks: list[asyncio.Task] = []
        self._stopped = False
        self._tap_fn = self._tap  # one bound object so identity checks work
        self.stats = {"sessions": 0, "wakes": 0, "seconds": 0.0, "barge_ins": 0, "usd": 0.0}

    @property
    def running(self) -> bool:
        return not self._stopped

    async def start(self) -> None:
        self.player.live_tap = self._tap_fn
        self._tasks = [asyncio.create_task(self._pace()), asyncio.create_task(self._monitor())]

    async def stop(self, reason: str) -> None:
        if self._stopped:
            return
        self._stopped = True
        if getattr(self.player, "live_tap", None) is self._tap_fn:
            self.player.live_tap = None
        for task in self._tasks:
            if task is not asyncio.current_task():
                task.cancel()
        await asyncio.gather(*(t for t in self._tasks if t is not asyncio.current_task()), return_exceptions=True)
        await self._close_session(reason)
        await self._clear_output()
        if self.on_stopped is not None:
            await self.on_stopped(self, reason)

    async def on_wake(self, event, outcome: str) -> None:
        async with self._wake_lock:
            await self._on_wake_locked(event, outcome)

    async def _on_wake_locked(self, event, outcome: str) -> None:
        if self._stopped or outcome == "busy":
            return
        if (self.session is not None and self.session.connected
                and self.active_speaker == event.speaker.user_id
                and self._input_open and outcome in ("extended", "held")):
            # The wake recognizer finalizes after audio was already forwarded.
            # Do not replay that utterance or clear the reply a second time.
            return
        if self.session is not None and (
            self.active_speaker != event.speaker.user_id or not self.session.connected
            or (not self._input_open and self._reply_seen and self._followup_until is None)
        ):
            # One immutable requester per API session. Closing invalidates late
            # callbacks before they can act on behalf of the next speaker.
            await self._close_session("speaker_changed_or_reconnect")
        self.active_speaker = event.speaker.user_id
        self.active_name = event.speaker.display_name
        if self._tool_scope is None:
            scope = object()
            self._tool_scope = scope
            self._tool_actor = Actor(
                event.speaker.user_id, event.speaker.guild_id, event.speaker.channel_id,
                lambda: self._tool_scope is scope and not self._stopped,
            )
        self.stats["wakes"] += 1
        try:
            await self._ensure_session()
        except LimitReached as exc:
            await self._close_session("spend cap reached")
            await self.announce(f"Can't talk right now: {exc}")
            return
        except Exception as exc:
            await self._close_session("connect failed")
            logger.warning("GPT-Live connect failed: %s: %s", type(exc).__name__, exc)
            await self.announce("Couldn't reach GPT-Live; try again in a moment.")
            return
        self.active_speaker = event.speaker.user_id
        self.active_name = event.speaker.display_name
        self._in_buf.clear()
        self._in_state = None
        await self._clear_output()
        identity = (
            f"Current speaker: {event.speaker.display_name} (Discord user id {event.speaker.user_id}). "
            "This identity is verified by the application; address them by this name."
        )
        notes = self.memory.recall(event.speaker.user_id) if self.memory is not None else []
        if notes:
            identity += " Remembered about them: " + "; ".join(notes) + "."
        await self.session.append_instructions(identity)
        await self.session.append_audio(event.audio)
        # Discord sends packets only while someone speaks; GPT-Live's turn
        # detection needs a continuous stream, so from here the monitor pads
        # real time with silence until the window closes.
        self._stream_started = self._clock()
        self._sent_samples = 0
        self._last_activity = self._clock()
        self._begin_input()
        self.player.window.wake(event.speaker.user_id)
        self.player.window.touch(event.speaker.user_id)

    def _begin_input(self):
        self._input_open = True
        self._last_input_speech = self._clock()
        self._reply_seen = False
        self._followup_until = None
        self._followup_blocked = False

    def _update_attention(self):
        if self.session is None or not self.session.connected:
            return
        now = self._clock()
        if self._input_open and now - self._last_input_speech >= self.config.input_silence_s:
            self._input_open = False
        # GPT-Live has no output-audio-done event. Use audible output, local
        # playback drain and a quiet margin; never infer turns from transcripts.
        if (not self._input_open and self._reply_seen and self._followup_until is None
                and not self._out_buf and now >= self._playback_until + 0.8
                and now - self._last_output_speech >= 0.8):
            self._followup_until = now + (0 if self._followup_blocked else self.config.followup_s)
        if self._followup_until is not None and now >= self._followup_until:
            if self.player.window.active == self.active_speaker:
                self.player.window.close()
        elif self.active_speaker is not None:
            self.player.window.touch(self.active_speaker)

    def _tap(self, speaker, pcm48: bytes) -> None:
        if self.session is None or not self.session.connected:
            return
        self._update_attention()
        if speaker.user_id != self.active_speaker:
            if not self._input_open and not _is_silence(pcm48):
                # Other people resuming conversation cancel implicit follow-ups.
                self._followup_blocked = True
                if self._followup_until is not None:
                    self._followup_until = self._clock()
                    self._update_attention()
            return
        if not self.player.window.is_open_for(speaker.user_id) or _is_silence(pcm48):
            return
        if not self._input_open:
            if self._followup_until is None or self._clock() >= self._followup_until:
                return
            self._begin_input()
        pcm16, self._in_state = resample(pcm48, self._in_state)
        self._in_buf += pcm16
        self._last_input_speech = self._clock()
        self._last_activity = self._clock()
        # Packet arrival alone must never wipe an in-progress reply. A fresh
        # explicit wake can interrupt; accepted speech uses GPT-Live's policy.

    async def _on_audio(self, pcm16k: bytes) -> None:
        if self._stopped or not pcm16k:
            return
        # GPT-Live's own docs say audio-delta chunk boundaries are arbitrary;
        # nothing guarantees each delta ends on a 16-bit sample boundary.
        pcm16k = self._out_pending + pcm16k
        if len(pcm16k) % 2:
            self._out_pending, pcm16k = pcm16k[-1:], pcm16k[:-1]
        else:
            self._out_pending = b""
        if not pcm16k:
            return
        # GPT-Live streams silence between turns; it must not duck the music,
        # keep the session "active" or hold the command window open.
        if _is_silence(pcm16k):
            # Retain at most 200 ms after real speech. Otherwise continuous
            # silent deltas replenish the tail forever and hold everyone busy.
            remaining = max(0, OUT_RATE // 5 - self._output_silence_samples)
            self._output_silence_samples += len(pcm16k) // 2
            if self._out_buf and remaining:
                pcm48, self._out_state = upsample_to_discord(pcm16k[:remaining * 2], self._out_state)
                self._out_buf += pcm48  # finish the tail of a real utterance smoothly
            return
        self._output_silence_samples = 0
        if self.config.speech_gain != 1.0 and audioop is not None:
            pcm16k = audioop.mul(pcm16k, 2, self.config.speech_gain)  # saturating
        pcm48, self._out_state = upsample_to_discord(pcm16k, self._out_state)
        self._out_buf += pcm48
        self._last_activity = self._clock()
        self._last_output_speech = self._clock()
        self._reply_seen = True

    async def _pace(self) -> None:
        idle_ticks = 0
        while True:
            await asyncio.sleep(PACE_S)
            chunk = len(self._out_buf) // OUT_FRAME * OUT_FRAME
            chunk = min(chunk, OUT_FRAME * 3)
            if chunk:
                idle_ticks = 0
                frame = bytes(self._out_buf[:chunk])
                del self._out_buf[:chunk]
            elif self._out_buf and idle_ticks >= 3:
                frame = bytes(self._out_buf) + b"\0" * (OUT_FRAME - len(self._out_buf))
                self._out_buf.clear()
            else:
                idle_ticks += 1
                continue
            try:
                await self.player.speak(frame)
                self._playback_until = max(self._clock(), self._playback_until) + len(frame) / 192000
            except Exception:
                logger.warning("Could not send speech to the voice transport")
                self._out_buf.clear()

    def _pad_silence(self) -> None:
        if not self._stream_started or self.active_speaker is None:
            return
        expected = int((self._clock() - self._stream_started) * OUT_RATE)
        have = self._sent_samples + len(self._in_buf) // 2
        missing = min(expected - have, OUT_RATE)  # never more than 1 s per tick
        if missing > OUT_RATE // 50:  # 20 ms
            self._in_buf += b"\0" * (missing * 2)

    async def _monitor(self) -> None:
        last_report = self._clock()
        while True:
            await asyncio.sleep(0.25)
            self._update_attention()
            self._pad_silence()
            if self._in_buf and self.session is not None and self.session.connected:
                pcm = bytes(self._in_buf)
                self._in_buf.clear()
                self._sent_samples += len(pcm) // 2
                try:
                    await self.session.append_audio(pcm)
                except (ConnectionError, Exception):
                    logger.warning("Dropping input audio; GPT-Live socket unavailable")
            if self.session is not None and self.session.connected and self._clock() - last_report >= 10:
                last_report = self._clock()
                self._account(self.session.connected_seconds())
                logger.info(
                    "GPT-Live %.0fs: sent %.1fs audio, pending out %d ms, events %s",
                    self.session.connected_seconds(), self._sent_samples / OUT_RATE,
                    len(self._out_buf) // OUT_FRAME * 20, self.session.usage.events,
                    extra={"guild_id": self.player.guild.id},
                )
            if not self.player.is_connected() or not self.player.audio.active:
                asyncio.get_running_loop().create_task(self.stop("listener ended"))
                return
            if self.session is None:
                continue
            if not self.session.connected:
                await self._close_session(self.session.usage.close_reason or "socket closed")
                continue
            connected = self.session.connected_seconds()
            if connected >= self.config.session_max_s or connected >= self._allowance_s:
                await self._close_session("session limit reached")
                await self.announce(
                    f"GPT-Live session closed after {connected / 60:.1f} min (limit). Say my name to start another."
                )
                continue
            if not self._warned and connected >= self.config.warn_at_s:
                self._warned = True
                await self.announce(f"Heads up: this GPT-Live session hits its limit in {(self.config.session_max_s - connected) / 60:.0f} min.")
            if self._clock() - self._last_activity > self.config.idle_s and not self._out_buf:
                await self._close_session("idle")

    async def _ensure_session(self) -> None:
        if self.session is not None and self.session.connected:
            return
        allowance = self.ledger.allowance_s(self.config)
        if allowance < 15:
            day_usd, month_usd = self.ledger.usd(self.config.price_per_minute)
            raise LimitReached(
                f"spend cap reached (today ${day_usd:.2f}/{self.config.daily_usd:.2f}, "
                f"month ${month_usd:.2f}/{self.config.monthly_usd:.2f})."
            )
        self._allowance_s = allowance
        self._warned = False
        self._accounted_s = 0.0
        actor = self._tool_actor

        async def tool_call(name, arguments):
            return await self._on_tool_call(name, arguments, actor=actor)

        async def audio(pcm):
            if actor is not None and actor.valid():
                await self._on_audio(pcm)

        async def event(kind, payload):
            if actor is not None and actor.valid():
                await self._on_event(kind, payload)

        session = self._session_factory(
            self.config, on_audio=audio, on_event=event, on_tool_call=tool_call,
        )
        try:
            await session.connect()
        except BaseException:
            await session.close(reason="connect failed")
            raise
        self.session = session
        self.stats["sessions"] += 1
        self._last_activity = self._clock()
        logger.info("GPT-Live session started (%s), allowance %.0fs", session.session_id or "?", allowance,
                    extra={"guild_id": self.player.guild.id})

    async def _on_event(self, kind: str, event: dict) -> None:
        if kind in ("session.input_transcript.delta", "session.output_transcript.delta"):
            logger.debug("%s: %s", kind.split(".")[1], str(event.get("delta", ""))[:120])
        elif kind == "session.closed":
            logger.info("GPT-Live session closed: %s", event.get("reason"), extra={"guild_id": self.player.guild.id})

    async def _on_tool_call(self, name: str, arguments_json: str, *, actor=None) -> str:
        tool = self.tools.get(name)
        if tool is None:
            logger.warning("Unknown tool call: %s", name, extra={"guild_id": self.player.guild.id})
            return json.dumps({"error": "unknown tool"})
        try:
            if len(arguments_json) > 4096:
                raise ValueError("Arguments too large")
            arguments = json.loads(arguments_json)
            validate_arguments(name, arguments)
        except (ValueError, TypeError) as exc:
            logger.warning("Rejected tool arguments for %s: %s", name, type(exc).__name__)
            return json.dumps({"error": str(exc)})
        if actor is None or not actor.valid():
            return json.dumps({"error": "The requesting speaker's session has expired. Please ask again."})
        logger.info("Tool call: %s", name, extra={"guild_id": actor.guild_id, "user_id": actor.user_id})
        token = current_actor.set(actor)
        try:
            result = await tool(arguments)
            return json.dumps(result)[:4000]
        finally:
            current_actor.reset(token)

    async def _close_session(self, reason: str) -> None:
        self._tool_scope = None
        self._tool_actor = None
        session, self.session = self.session, None
        self.active_speaker = None
        self._input_open = False
        self._reply_seen = False
        self._followup_until = None
        self._stream_started = 0.0
        self.player.window.close()
        self._in_buf.clear()
        if session is None:
            return
        usage = await session.close(reason=reason)
        seconds = usage.reported_s if usage.reported_s is not None else usage.connected_s
        self._account(seconds)
        usd = seconds / 60 * self.config.price_per_minute
        logger.info(
            "GPT-Live session ended: %s | %.0fs | $%.3f | tokens in/out %d/%d",
            reason, seconds, usd, usage.input_tokens, usage.output_tokens,
            extra={"guild_id": self.player.guild.id},
        )

    def _account(self, total_seconds: float) -> None:
        """Record spend incrementally so a crash or restart mid-session is not lost."""
        delta = max(0.0, total_seconds - self._accounted_s)
        if delta <= 0:
            return
        self._accounted_s = total_seconds
        self.ledger.add(delta)
        self.stats["seconds"] += delta
        self.stats["usd"] += delta / 60 * self.config.price_per_minute

    async def _clear_output(self) -> None:
        self._out_buf.clear()
        self._out_state = None
        self._out_pending = b""
        self._output_silence_samples = 0
        self._playback_until = self._clock()
        await self._speak_clear()

    async def _speak_clear(self) -> None:
        try:
            await self.player.speak_clear()
        except Exception:
            pass
