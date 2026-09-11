"""Mafic controls plus a single, receive-capable Discord voice transport.

Requires the pinned external_bridge Lavalink fork; stock Lavalink is rejected
by the bridge WebSocket handshake before any Discord voice connection starts.
"""

import asyncio
import base64
import contextlib
import json
import logging
import os
from pathlib import Path
import time

import mafic

from elbot.config import get_lavalink_connection_info
from .audio_input import Speaker, SpeakerAudio


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
        self.receive_errors = 0

    async def _send(self, payload):
        async with self._send_lock:
            if self._process is None or self._process.returncode is not None:
                raise RuntimeError("Voice transport is unavailable")
            data = (json.dumps(payload, separators=(",", ":")) + "\n").encode()
            if len(data) > 65536:
                raise ValueError("Voice control message exceeds limit")
            self._process.stdin.write(data)
            await asyncio.wait_for(self._process.stdin.drain(), 2)

    def _human_members(self):
        return {m.id for m in self.channel.members if not m.bot}

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
            stderr=asyncio.subprocess.DEVNULL, env=env, limit=65536,
        )
        self._reader = asyncio.create_task(self._read())
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
                elif op == "receive_error":
                    self.receive_errors += 1
                elif op == "failed":
                    break
        except (ValueError, KeyError, TypeError, OSError):
            logger.warning("Shared voice transport returned invalid data or closed")
        finally:
            self._bridge_failed.set()
            self._bridge_ready.clear()
            self._connected = False
            self.audio.stop()
            if not self._closing:
                logger.warning("Shared voice transport stopped", extra={"guild_id": self.guild.id})

    def _receive(self, message):
        if (not self.audio.active or time.monotonic() >= self._listen_until
                or message.get("generation") != self._generation):
            return
        member = self.guild.get_member(int(message["user_id"]))
        if (member is None or member.bot
                or getattr(getattr(member, "voice", None), "channel", None) != self.channel):
            return
        ssrc = int(message["ssrc"])
        speaker = Speaker(member.id, member.display_name, self.guild.id, self.channel.id)
        if self.audio.bind(self._generation, ssrc, speaker):
            pcm = base64.b64decode(message["pcm"], validate=True)
            self.audio.feed(self._generation, ssrc, pcm)

    async def start_listening(self, seconds=120):
        if not self.is_connected():
            raise RuntimeError("Voice transport is not connected")
        if self.channel.id != self.audio.channel_id:
            self.audio = SpeakerAudio(self.guild.id, self.channel.id)
        self._generation = self.audio.start()
        self._listen_until = time.monotonic() + min(max(seconds, 1), 120)
        self._members = self._human_members()
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
        if self._process and self._process.returncode is None:
            await self._send({"op": "listen", "enabled": False})

    async def _tick(self):
        try:
            await self._tick_loop()
        except (OSError, RuntimeError, asyncio.TimeoutError):
            self.audio.stop()
            self._bridge_failed.set()
            self._connected = False
            logger.warning("Voice receiver control failed; local listening stopped")

    async def _tick_loop(self):
        while not self._closing:
            await asyncio.sleep(0.1)
            if self.audio.active:
                members = self._human_members()
                if time.monotonic() >= self._listen_until or not members:
                    await self.stop_listening()
                elif members != self._members:
                    for user_id in self._members - members:
                        self.audio.remove(user_id)
                    self._members = members
                    await self._send({"op": "members", "members": [str(i) for i in members]})
            for event in self.audio.tick():
                logger.info("%s | %s", event.kind, event.speaker.display_name, extra={
                    "guild_id": event.speaker.guild_id, "user_id": event.speaker.user_id,
                })

    async def disconnect(self, *, force=False):
        if self._closing:
            return
        self._closing = True
        self.audio.stop()
        self._bridge_ready.clear()
        self._connected = False
        try:
            if self._process and self._process.returncode is None:
                with contextlib.suppress(Exception):
                    await self._send({"op": "close"})
                try:
                    await asyncio.wait_for(self._process.wait(), 1)
                except asyncio.TimeoutError:
                    self._process.kill()
                    await self._process.wait()
            tasks = [task for task in (self._reader, self._ticker) if task and task is not asyncio.current_task()]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await super().disconnect(force=True)
        finally:
            self.cleanup()
