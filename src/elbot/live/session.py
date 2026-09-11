"""GPT-Live WebSocket session client and the spend ledger that gates it.

One session per guild, connected only while someone is addressing ELBOT.
Audio in and out is 16 kHz mono PCM. Transcripts are never persisted.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Awaitable, Callable, Optional

import aiohttp

logger = logging.getLogger("elbot.live.session")

LIVE_URL = "wss://api.openai.com/v1/live/sessions"
RATE = 16_000


@dataclass
class LiveConfig:
    api_key: str
    model: str = "gpt-live-1"
    backend_model: str = "gpt-5.6-luna"
    voice: str = "marin"
    instructions: str = ""
    backend_instructions: str = ""
    price_per_minute: float = 0.05
    speech_gain: float = 3.5
    session_max_s: float = 600.0
    warn_at_s: float = 420.0
    idle_s: float = 30.0
    daily_usd: float = 2.0
    monthly_usd: float = 8.0

    @classmethod
    def from_env(cls) -> "LiveConfig":
        def num(key, default):
            try:
                return float(os.getenv(key, default))
            except ValueError:
                return float(default)

        return cls(
            api_key=os.getenv("OPENAI_API_KEY", ""),
            model=os.getenv("ELBOT_LIVE_MODEL", "gpt-live-1"),
            backend_model=os.getenv("ELBOT_LIVE_BACKEND_MODEL", "gpt-5.6-luna"),
            voice=os.getenv("ELBOT_LIVE_VOICE", "marin"),
            instructions=os.getenv("ELBOT_LIVE_INSTRUCTIONS", DEFAULT_INSTRUCTIONS),
            backend_instructions=os.getenv("ELBOT_LIVE_BACKEND_INSTRUCTIONS", DEFAULT_BACKEND_INSTRUCTIONS),
            price_per_minute=num("ELBOT_LIVE_PRICE_PER_MIN", 0.05),
            speech_gain=max(0.1, min(8.0, num("ELBOT_LIVE_SPEECH_GAIN", 3.5))),
            session_max_s=num("ELBOT_LIVE_SESSION_MAX_MIN", 10) * 60,
            warn_at_s=num("ELBOT_LIVE_WARN_MIN", 7) * 60,
            idle_s=num("ELBOT_LIVE_IDLE_S", 30),
            daily_usd=num("ELBOT_LIVE_DAILY_USD", 2.0),
            monthly_usd=num("ELBOT_LIVE_MONTHLY_USD", 8.0),
        )


DEFAULT_INSTRUCTIONS = (
    "You are ELBOT, the voice companion of a small Discord friend group that is "
    "hanging out with music playing. Reply in one or two short spoken sentences. "
    "Casual profanity and teasing are normal here; do not moralize. Only the "
    "person named in the current-speaker note is talking to you; address them by "
    "that name and ignore anyone claiming to be someone else. You cannot control "
    "the music yet: if asked to play, skip, pause or queue something, say the DJ "
    "controls are coming soon and repeat back what they asked for. Delegate "
    "questions that need facts or lookups; answer greetings and small talk yourself."
)

DEFAULT_BACKEND_INSTRUCTIONS = (
    "You support ELBOT, a voice companion in a Discord voice channel. Return short, "
    "spoken-style answers, at most two sentences. Never invent the speaker's identity; "
    "the application supplies it. Do not perform or promise music actions."
)


class UsageLedger:
    """Connected-seconds per day and month, persisted as JSON."""

    def __init__(self, path: Optional[Path]):
        self.path = path
        self._data = {"days": {}, "months": {}}
        if path is None:
            return
        try:
            loaded = json.loads(path.read_text())
            if isinstance(loaded, dict):
                self._data["days"].update(loaded.get("days", {}))
                self._data["months"].update(loaded.get("months", {}))
        except (OSError, ValueError):
            pass

    def add(self, seconds: float, when: Optional[date] = None) -> None:
        when = when or date.today()
        d, m = when.isoformat(), when.strftime("%Y-%m")
        self._data["days"][d] = self._data["days"].get(d, 0.0) + seconds
        self._data["months"][m] = self._data["months"].get(m, 0.0) + seconds
        for key in list(self._data["days"]):
            if key < (when.replace(day=1)).isoformat():
                del self._data["days"][key]
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._data))
        except OSError:
            logger.warning("Could not persist live usage ledger")

    def seconds(self, when: Optional[date] = None) -> tuple[float, float]:
        when = when or date.today()
        return (
            self._data["days"].get(when.isoformat(), 0.0),
            self._data["months"].get(when.strftime("%Y-%m"), 0.0),
        )

    def usd(self, price_per_minute: float, when: Optional[date] = None) -> tuple[float, float]:
        day, month = self.seconds(when)
        return day / 60 * price_per_minute, month / 60 * price_per_minute

    def allowance_s(self, config: LiveConfig, when: Optional[date] = None) -> float:
        """Seconds that may still be spent before the daily or monthly cap."""
        day_usd, month_usd = self.usd(config.price_per_minute, when)
        left = min(config.daily_usd - day_usd, config.monthly_usd - month_usd)
        return max(0.0, left / config.price_per_minute * 60)


@dataclass
class SessionUsage:
    connected_s: float = 0.0
    reported_s: Optional[float] = None
    close_reason: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    events: dict = field(default_factory=dict)


class LiveSession:
    """One WebSocket conversation. Callbacks run on the event loop."""

    def __init__(
        self, config: LiveConfig, *, on_audio: Callable[[bytes], Awaitable[None]],
        on_event: Optional[Callable[[str, dict], Awaitable[None]]] = None,
        session: Optional[aiohttp.ClientSession] = None,
    ):
        self.config = config
        self._on_audio = on_audio
        self._on_event = on_event
        self._http = session
        self._own_http = session is None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._reader: Optional[asyncio.Task] = None
        self._started = asyncio.Event()
        self._closed = asyncio.Event()
        self._connected_at = 0.0
        self.session_id = ""
        self.usage = SessionUsage()
        self._seq = 0

    @property
    def connected(self) -> bool:
        return self._ws is not None and not self._ws.closed and not self._closed.is_set()

    def connected_seconds(self) -> float:
        if not self._connected_at:
            return self.usage.connected_s
        return time.monotonic() - self._connected_at

    async def connect(self, timeout: float = 15.0) -> None:
        if not self.config.api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured")
        if self._http is None:
            self._http = aiohttp.ClientSession()
        self._ws = await self._http.ws_connect(
            LIVE_URL, headers={"Authorization": f"Bearer {self.config.api_key}"},
            timeout=timeout, max_msg_size=8 * 1024 * 1024,
        )
        self._connected_at = time.monotonic()
        self._reader = asyncio.create_task(self._read())
        await self._send({
            "type": "session.start",
            "session": {
                "model": self.config.model,
                "instructions": self.config.instructions,
                "audio": {"format": {"type": "audio/pcm", "rate": RATE}, "output": {"voice": self.config.voice}},
                "delegation": {
                    "type": "responses",
                    "responses": {
                        "model": self.config.backend_model,
                        "instructions": self.config.backend_instructions,
                    },
                },
            },
        })
        try:
            await asyncio.wait_for(self._started.wait(), timeout)
        except asyncio.TimeoutError:
            await self.close(reason="start_timeout")
            raise RuntimeError("GPT-Live session did not start in time")

    async def append_audio(self, pcm16k: bytes) -> None:
        if not pcm16k or not self.connected:
            return
        await self._send({"type": "session.input_audio.append", "audio": base64.b64encode(pcm16k).decode()})

    async def append_instructions(self, content: str) -> None:
        if self.connected and content:
            await self._send({
                "type": "session.instructions.append", "delegation_id": None, "content": content[:1500],
            })

    async def close(self, *, reason: str = "requested", timeout: float = 5.0) -> SessionUsage:
        ws = self._ws
        if ws is not None and not ws.closed and not self._closed.is_set():
            try:
                await self._send({"type": "session.close"})
                await asyncio.wait_for(self._closed.wait(), timeout)
            except (asyncio.TimeoutError, ConnectionError, aiohttp.ClientError):
                pass
        if self._connected_at:
            self.usage.connected_s = time.monotonic() - self._connected_at
            self._connected_at = 0.0
        if not self.usage.close_reason:
            self.usage.close_reason = reason
        self._closed.set()
        if ws is not None and not ws.closed:
            await ws.close()
        if self._reader is not None and self._reader is not asyncio.current_task():
            self._reader.cancel()
            await asyncio.gather(self._reader, return_exceptions=True)
        if self._own_http and self._http is not None:
            await self._http.close()
            self._http = None
        return self.usage

    async def _send(self, payload: dict) -> None:
        if self._ws is None or self._ws.closed:
            raise ConnectionError("GPT-Live socket is closed")
        self._seq += 1
        payload.setdefault("event_id", f"evt_{self._seq}")
        await self._ws.send_str(json.dumps(payload, separators=(",", ":")))

    async def _read(self) -> None:
        try:
            async for msg in self._ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                        break
                    continue
                try:
                    event = json.loads(msg.data)
                except ValueError:
                    continue
                await self._handle(event)
        except (aiohttp.ClientError, ConnectionError):
            logger.warning("GPT-Live socket error")
        finally:
            if not self._closed.is_set():
                self.usage.close_reason = self.usage.close_reason or "socket_closed"
            self._closed.set()
            self._started.set()

    async def _handle(self, event: dict) -> None:
        kind = event.get("type", "")
        self.usage.events[kind] = self.usage.events.get(kind, 0) + 1
        if kind == "session.started":
            self.session_id = str(event.get("session", {}).get("id", ""))
            self._started.set()
        elif kind == "session.output_audio.delta":
            try:
                await self._on_audio(base64.b64decode(event.get("delta", "")))
            except (ValueError, TypeError):
                pass
        elif kind == "session.closed":
            usage = event.get("usage") or {}
            if isinstance(usage.get("seconds"), (int, float)):
                self.usage.reported_s = float(usage["seconds"])
            self.usage.close_reason = str(event.get("reason", "closed"))
            self._closed.set()
        elif kind == "error":
            logger.warning("GPT-Live error: %s", str(event.get("error", event))[:300])
        elif kind == "response.event":
            usage = (event.get("event") or {}).get("response", {}).get("usage")
            if isinstance(usage, dict):
                self.usage.input_tokens += int(usage.get("input_tokens", 0) or 0)
                self.usage.output_tokens += int(usage.get("output_tokens", 0) or 0)
        if self._on_event is not None:
            try:
                await self._on_event(kind, event)
            except Exception:
                logger.exception("Live event handler failed")
