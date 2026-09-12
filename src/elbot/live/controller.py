"""Per-guild GPT-Live orchestration.

Armed by /live start. The socket is opened only when someone addresses ELBOT
and closed again after idle, so connected (billed) time tracks real use.
Only the active speaker's audio inside an open command window is forwarded.
"""

from __future__ import annotations

import asyncio
import json
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
BARGE_IN_FRAMES = 15  # the speaker must keep talking ~300 ms to cut the bot off


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
        self._in_buf = bytearray()
        self._in_state = None
        self._stream_started = 0.0
        self._sent_samples = 0
        self._out_buf = bytearray()
        self._out_state = None
        self._out_pending = b""  # a byte held back when a delta splits a 16-bit sample
        self._barge_frames = 0
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
        if self._stopped or outcome == "busy":
            return
        self.stats["wakes"] += 1
        try:
            await self._ensure_session()
        except LimitReached as exc:
            await self.announce(f"Can't talk right now: {exc}")
            return
        except Exception as exc:
            logger.warning("GPT-Live connect failed: %s: %s", type(exc).__name__, exc)
            await self.announce("Couldn't reach GPT-Live; try again in a moment.")
            return
        self.active_speaker = event.speaker.user_id
        self.active_name = event.speaker.display_name
        self._in_buf.clear()
        self._in_state = None
        if self._out_buf:
            # Diagnostic (2026-09-12): user reports ELBOT's own speech
            # sometimes cuts off mid-reply "randomly". A wake re-trigger
            # (outcome extended/held, e.g. a Vosk false-positive on "elbow"
            # from ongoing chatter, not just a fresh "opened" command)
            # unconditionally wipes any still-playing reply here. Logging
            # this to see whether that's actually what's happening before
            # changing the behavior.
            logger.info(
                "Wake (%s) cut off %d ms of pending ELBOT speech",
                outcome, len(self._out_buf) // OUT_FRAME * 20,
                extra={"guild_id": self.player.guild.id},
            )
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
        self.player.window.touch(event.speaker.user_id)

    def _tap(self, speaker, pcm48: bytes) -> None:
        if self.session is None or not self.session.connected or speaker.user_id != self.active_speaker:
            return
        if not self.player.window.is_open_for(speaker.user_id):
            return
        pcm16, self._in_state = resample(pcm48, self._in_state)
        self._in_buf += pcm16
        self.player.window.touch(speaker.user_id)
        self._last_activity = self._clock()
        if len(self._out_buf) > BARGE_IN_BYTES:
            self._barge_frames += 1
            if self._barge_frames >= BARGE_IN_FRAMES:
                self._barge_frames = 0
                self.stats["barge_ins"] += 1
                # Diagnostic (2026-09-12): see the matching note in on_wake.
                # This fires whenever the active speaker keeps talking for
                # ~300ms while ELBOT is still replying, whether or not they
                # meant to interrupt it - a candidate for "random" cutoffs.
                logger.info(
                    "Barge-in: clearing %d ms of pending ELBOT speech",
                    len(self._out_buf) // OUT_FRAME * 20,
                    extra={"guild_id": self.player.guild.id},
                )
                self._out_buf.clear()
                self._out_state = None
                asyncio.get_running_loop().create_task(self._speak_clear())
        else:
            self._barge_frames = 0

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
            if self._out_buf:
                pcm48, self._out_state = upsample_to_discord(pcm16k, self._out_state)
                self._out_buf += pcm48  # finish the tail of a real utterance smoothly
            return
        if self.config.speech_gain != 1.0 and audioop is not None:
            pcm16k = audioop.mul(pcm16k, 2, self.config.speech_gain)  # saturating
        pcm48, self._out_state = upsample_to_discord(pcm16k, self._out_state)
        self._out_buf += pcm48
        self._last_activity = self._clock()
        if self.active_speaker is not None:
            self.player.window.touch(self.active_speaker)

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
            except Exception:
                logger.warning("Could not send speech to the voice transport")
                self._out_buf.clear()

    def _pad_silence(self) -> None:
        if not self._stream_started or self.active_speaker is None:
            return
        if not self.player.window.is_open_for(self.active_speaker):
            self._stream_started = 0.0
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
        session = self._session_factory(
            self.config, on_audio=self._on_audio, on_event=self._on_event, on_tool_call=self._on_tool_call,
        )
        await session.connect()
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

    async def _on_tool_call(self, name: str, arguments_json: str) -> str:
        tool = self.tools.get(name)
        if tool is None:
            logger.warning("Unknown tool call: %s", name, extra={"guild_id": self.player.guild.id})
            return json.dumps({"error": "unknown tool"})
        try:
            arguments = json.loads(arguments_json) if arguments_json else {}
        except ValueError:
            arguments = {}
        logger.info("Tool call: %s(%s)", name, arguments, extra={"guild_id": self.player.guild.id})
        result = await tool(arguments)
        return json.dumps(result)[:4000]

    async def _close_session(self, reason: str) -> None:
        session, self.session = self.session, None
        self.active_speaker = None
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
        await self._speak_clear()

    async def _speak_clear(self) -> None:
        try:
            await self.player.speak_clear()
        except Exception:
            pass
