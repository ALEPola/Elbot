"""Mafic controls plus a single, receive-capable Discord voice transport.

Requires the pinned external_bridge Lavalink fork; stock Lavalink is rejected
by the bridge WebSocket handshake before any Discord voice connection starts.
"""

import asyncio
import base64
from collections import deque
import contextlib
import json
import logging
import os
from pathlib import Path
import time

import mafic

from elbot.config import get_lavalink_connection_info
from .audio_input import Speaker, SpeakerAudio
from .wake_word import CommandWindow, create_detector


logger = logging.getLogger("elbot.live")


class BridgePlayer(mafic.Player):
    def __init__(self, client, channel):
        super().__init__(client, channel)
        self.audio = SpeakerAudio(self.guild.id, channel.id)
        self._generation = 0
        self._process = None
        self._reader = None
        self._ticker = None
        self._bridge_ready = asyncio.Event()
        self._bridge_failed = asyncio.Event()
        self._send_lock = asyncio.Lock()
        self._closing = False
        self._listen_until = 0.0
        self._members = set()
        self._voice_ids = set()
        self._member_cache = {}
        self._fetching = set()
        self._stderr_reader = None
        self._stderr_tail = deque(maxlen=5)
        self._failure_reported = False
        self.receive_errors = 0
        self.wake = None
        self.window = CommandWindow()
        self.wake_count = 0
        self._notify = None
        self.live_tap = None

    async def _send(self, payload):
        async with self._send_lock:
            if self._process is None or self._process.returncode is not None:
                raise RuntimeError("Voice transport is unavailable")
            data = (json.dumps(payload, separators=(",", ":")) + "\n").encode()
            if len(data) > 65536:
                raise ValueError("Voice control message exceeds limit")
            self._process.stdin.write(data)
            await asyncio.wait_for(self._process.stdin.drain(), 2)

    def _member(self, user_id):
        return self.guild.get_member(user_id) or self._member_cache.get(user_id)

    def _voice_user_ids(self):
        return {uid for uid in self.channel.voice_states if uid != self.client.user.id}

    def _human_members(self):
        ids = set()
        for uid in self._voice_user_ids():
            member = self._member(uid)
            if member is not None and not member.bot:
                ids.add(uid)
        return ids

    async def _refresh_members(self):
        # Without the privileged members intent only the bot itself is cached,
        # so occupants come from voice states and are fetched over REST once.
        for uid in self._voice_user_ids():
            if self._member(uid) is None and uid not in self._fetching:
                self._fetching.add(uid)
                try:
                    self._member_cache[uid] = await self.guild.fetch_member(uid)
                except Exception:
                    pass
                finally:
                    self._fetching.discard(uid)
        for uid in list(self._member_cache):
            if uid not in self.channel.voice_states:
                del self._member_cache[uid]
        self._voice_ids = self._voice_user_ids()

    async def connect(self, *, timeout, reconnect, **kwargs):
        host, port, _, secure = get_lavalink_connection_info()
        bridge_url = os.getenv("ELBOT_VOICE_BRIDGE_URL", f"{'wss' if secure else 'ws'}://{host}:{port}/bridge/v1")
        token = os.getenv("ELBOT_VOICE_BRIDGE_TOKEN")
        if not token:
            raise RuntimeError("ELBOT_VOICE_BRIDGE_TOKEN must be configured")
        script = Path(__file__).with_name("transport") / "bridge.mjs"
        env = {key: value for key, value in os.environ.items() if key.upper() in {
            "PATH", "SYSTEMROOT", "WINDIR", "HOME", "USERPROFILE", "TEMP", "TMP",
        }}
        self._process = await asyncio.create_subprocess_exec(
            os.getenv("ELBOT_VOICE_NODE", "node"), str(script),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env=env, limit=65536,
        )
        self._reader = asyncio.create_task(self._read())
        self._stderr_reader = asyncio.create_task(self._read_stderr())
        self._ticker = asyncio.create_task(self._tick())
        try:
            await self._send({
                "op": "start", "guild_id": str(self.guild.id), "channel_id": str(self.channel.id),
                "bridge_url": bridge_url, "bridge_token": token,
            })
            ready = asyncio.create_task(self._wait_ready())
            failed = asyncio.create_task(self._bridge_failed.wait())
            try:
                done, _ = await asyncio.wait({ready, failed}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
                if failed in done or ready not in done:
                    raise RuntimeError("Shared voice transport failed or timed out; check bridge configuration")
                await ready
            finally:
                for task in (ready, failed):
                    task.cancel()
                await asyncio.gather(ready, failed, return_exceptions=True)
            self._connected = True
        except BaseException:
            await self.disconnect(force=True)
            raise

    async def _wait_ready(self):
        await self._bridge_ready.wait()
        await self._voice_state_update_event.wait()
        await self._voice_server_update_event.wait()

    async def on_voice_state_update(self, data):
        await self._send({"op": "voice_state", "data": data})
        if data.get("channel_id") is not None and int(data["channel_id"]) != self.channel.id:
            await self.stop_listening()
        await super().on_voice_state_update(data)

    async def on_voice_server_update(self, data):
        await self._send({"op": "voice_server", "data": data})
        await super().on_voice_server_update(data)

    def update_state(self, state):
        super().update_state(state)
        # The fork reports its frame poller's state, not Discord connectivity.
        self._connected = self._bridge_ready.is_set() and not self._bridge_failed.is_set() and not self._closing

    async def _read(self):
        try:
            while line := await self._process.stdout.readline():
                message = json.loads(line)
                op = message.get("op")
                if op == "gateway_send":
                    payload = message["payload"]
                    data = payload["d"]
                    if payload["op"] != 4 or str(data["guild_id"]) != str(self.guild.id):
                        raise ValueError("Invalid voice gateway request")
                    channel_id = data.get("channel_id")
                    channel = self.guild.get_channel(int(channel_id)) if channel_id else None
                    if channel_id and channel is None:
                        raise ValueError("Unknown voice channel")
                    await self.guild.change_voice_state(
                        channel=channel, self_mute=bool(data.get("self_mute")),
                        self_deaf=bool(data.get("self_deaf")),
                    )
                elif op == "ready":
                    self._bridge_ready.set()
                elif op == "voice_status":
                    if message["status"] != "ready":
                        self._bridge_ready.clear()
                        self._connected = False
                        self.audio.stop()
                    else:
                        self._bridge_ready.set()
                        self._connected = True
                elif op == "pcm":
                    self._receive(message)
                elif op == "diag":
                    logger.info(
                        "Node mixer 5s: speech_frames=%s speech_bytes_in=%s music_frames=%s "
                        "mixed=%s encode_errors=%s idle_ticks=%s",
                        message.get("speechFrames"), message.get("speechBytesIn"),
                        message.get("musicFrames"), message.get("mixed"),
                        message.get("encodeErrors"), message.get("silentTicks"),
                        extra={"guild_id": self.guild.id},
                    )
                elif op == "receive_error":
                    self.receive_errors += 1
                    if message.get("reason") == "backpressure":
                        logger.warning(
                            "Voice receiver dropping audio under backpressure (dropped=%s)",
                            message.get("dropped"), extra={"guild_id": self.guild.id},
                        )
                elif op == "failed":
                    logger.warning(
                        "Shared voice transport failed: %s", message.get("reason"),
                        extra={"guild_id": self.guild.id},
                    )
                    break
        except (ValueError, KeyError, TypeError, OSError):
            logger.warning("Shared voice transport returned invalid data or closed")
        finally:
            self._bridge_failed.set()
            self._bridge_ready.clear()
            self._connected = False
            self.audio.stop()
            if not self._closing:
                logger.warning(
                    "Shared voice transport stopped (exit=%s) %s",
                    self._process.returncode if self._process else None,
                    " | ".join(self._stderr_tail),
                    extra={"guild_id": self.guild.id},
                )
                self._report_failure()

    def _report_failure(self):
        if self._failure_reported or self._closing:
            return
        self._failure_reported = True
        # The music cog re-queues the current track and reconnects.
        self.client.dispatch("bridge_transport_failed", self)

    async def _read_stderr(self):
        try:
            while line := await self._process.stderr.readline():
                text = line.decode(errors="replace").strip()[:300]
                if text:
                    self._stderr_tail.append(text)
        except (OSError, ValueError):
            pass

    def _receive(self, message):
        if (not self.audio.active or time.monotonic() >= self._listen_until
                or message.get("generation") != self._generation):
            return
        member = self._member(int(message["user_id"]))
        if (member is None or member.bot
                or getattr(getattr(member, "voice", None), "channel", None) != self.channel):
            return
        ssrc = int(message["ssrc"])
        speaker = Speaker(member.id, member.display_name, self.guild.id, self.channel.id)
        if self.audio.bind(self._generation, ssrc, speaker):
            pcm = base64.b64decode(message["pcm"], validate=True)
            if self.audio.feed(self._generation, ssrc, pcm):
                if self.wake is not None:
                    self.wake.feed(speaker, pcm)
                if self.live_tap is not None:
                    try:
                        self.live_tap(speaker, pcm)
                    except Exception:
                        logger.exception("Live audio tap failed")
                        self.live_tap = None

    async def start_listening(self, seconds=120, *, notify=None):
        if not self.is_connected():
            raise RuntimeError("Voice transport is not connected")
        if self.channel.id != self.audio.channel_id:
            self.audio = SpeakerAudio(self.guild.id, self.channel.id)
        await self._refresh_members()
        members = self._human_members()
        if not members:
            raise RuntimeError("No human members in the voice channel")
        self._generation = self.audio.start()
        self._listen_until = time.monotonic() + min(max(seconds, 1), 3600)
        self._members = members
        self._notify = notify
        if self.wake is None:
            self.wake = await asyncio.get_running_loop().run_in_executor(None, create_detector)
        self.window.close()
        try:
            await self._send({
                "op": "listen", "enabled": True, "generation": self._generation,
                "members": [str(i) for i in self._members],
            })
        except BaseException:
            self.audio.stop()
            raise

    async def stop_listening(self):
        self.audio.stop()
        self._listen_until = 0
        self.window.close()
        wake, self.wake = self.wake, None
        if wake is not None:
            await asyncio.get_running_loop().run_in_executor(None, wake.close)
        if self._process and self._process.returncode is None:
            await self._send({"op": "listen", "enabled": False})

    async def speak(self, pcm48: bytes):
        """Queue 48 kHz stereo s16le bot speech; the transport ducks music under it."""
        if pcm48:
            await self._send({"op": "speak", "pcm": base64.b64encode(pcm48).decode()})

    async def speak_clear(self):
        if self._process and self._process.returncode is None:
            await self._send({"op": "speak_clear"})

    async def _handle_wake_events(self):
        if self.wake is None:
            return
        self.window.expire()
        for event in self.wake.poll():
            self.wake_count += 1
            outcome = self.window.wake(event.speaker.user_id)
            logger.info(
                "wake | %s | %s | %s", event.speaker.display_name, event.text, outcome,
                extra={"guild_id": event.speaker.guild_id, "user_id": event.speaker.user_id},
            )
            self.client.dispatch("wake_phrase", self, event, outcome)
            if self._notify is not None:
                with contextlib.suppress(Exception):
                    await self._notify(event, outcome)

    async def _tick(self):
        try:
            await self._tick_loop()
        except (OSError, RuntimeError, asyncio.TimeoutError):
            self.audio.stop()
            self._bridge_failed.set()
            self._connected = False
            logger.warning("Voice receiver control failed; local listening stopped")
            self._report_failure()

    async def _tick_loop(self):
        while not self._closing:
            await asyncio.sleep(0.1)
            if self.audio.active:
                if self._voice_user_ids() != self._voice_ids:
                    await self._refresh_members()
                members = self._human_members()
                if time.monotonic() >= self._listen_until or not members:
                    logger.info(
                        "Local listening ended: %s",
                        "time limit" if members else "no human members",
                        extra={"guild_id": self.guild.id},
                    )
                    await self.stop_listening()
                elif members != self._members:
                    for user_id in self._members - members:
                        self.audio.remove(user_id)
                        if self.wake is not None:
                            self.wake.remove(user_id)
                    self._members = members
                    await self._send({"op": "members", "members": [str(i) for i in members]})
            for event in self.audio.tick():
                logger.info("%s | %s", event.kind, event.speaker.display_name, extra={
                    "guild_id": event.speaker.guild_id, "user_id": event.speaker.user_id,
                })
                if event.kind == "speaker_stop" and self.wake is not None:
                    self.wake.flush(event.speaker.user_id)
            await self._handle_wake_events()

    async def disconnect(self, *, force=False):
        if self._closing:
            return
        self._closing = True
        self.audio.stop()
        self._bridge_ready.clear()
        self._connected = False
        wake, self.wake = self.wake, None
        if wake is not None:
            with contextlib.suppress(Exception):
                await asyncio.get_running_loop().run_in_executor(None, wake.close)
        try:
            if self._process and self._process.returncode is None:
                with contextlib.suppress(Exception):
                    await self._send({"op": "close"})
                try:
                    await asyncio.wait_for(self._process.wait(), 1)
                except asyncio.TimeoutError:
                    self._process.kill()
                    await self._process.wait()
            tasks = [
                task for task in (self._reader, self._stderr_reader, self._ticker)
                if task and task is not asyncio.current_task()
            ]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await super().disconnect(force=True)
        finally:
            self.cleanup()
