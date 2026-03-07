"""
Patch nextcord voice client compatibility issues.

- Guard poll loop when websocket implementations do not expose poll_event.
- Preserve Discord voice endpoint ports (e.g. :8443) during server update handling.
"""

import asyncio
import socket

from nextcord.voice_client import (
    ConnectionClosed,
    ExponentialBackoff,
    VoiceClient,
    _log,
)
from nextcord.utils import MISSING


def apply_patch() -> None:
    async def patched_on_voice_server_update(self: VoiceClient, data) -> None:
        # Mirrors upstream fix: keep endpoint port instead of stripping with
        # rpartition(":"), which breaks modern Discord voice endpoints.
        if self._voice_server_complete.is_set():
            _log.info(msg="Ignoring extraneous voice server update.")
            return

        self.token = data.get("token")
        self.server_id = int(data["guild_id"])
        endpoint = data.get("endpoint")

        if endpoint is None or self.token is None or self.token is MISSING:
            _log.warning(
                "Awaiting endpoint... This requires waiting. "
                "If timeout occurred considering raising the timeout and reconnecting."
            )
            return

        self.endpoint = endpoint.removeprefix("wss://")
        self.endpoint_ip = MISSING
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setblocking(False)

        if not self._handshaking:
            await self.ws.close(4000)
            return

        self._voice_server_complete.set()

    async def patched_poll_voice_ws(self: VoiceClient, reconnect: bool) -> None:
        backoff = ExponentialBackoff()
        missing_poll_event_logged = False
        while True:
            try:
                if self.ws and hasattr(self.ws, "poll_event"):
                    await self.ws.poll_event()
                    missing_poll_event_logged = False
                else:
                    # Prevent a tight no-await loop if the websocket implementation
                    # lacks poll_event (seen with newer websocket stacks).
                    if self.ws and not missing_poll_event_logged:
                        _log.warning(
                            "Voice websocket has no poll_event; waiting for reconnect path."
                        )
                        missing_poll_event_logged = True
                    await asyncio.sleep(0.1)
                    continue
            except (ConnectionClosed, asyncio.TimeoutError) as exc:
                if isinstance(exc, ConnectionClosed):
                    # The following close codes are undocumented so I will document them here.
                    # 1000 - normal closure (obviously)
                    # 4014 - voice channel has been deleted.
                    # 4015 - voice server has crashed
                    if exc.code in (1000, 4015):
                        _log.info(
                            "Disconnecting from voice normally, close code %d.",
                            exc.code,
                        )
                        await self.disconnect()
                        break
                    if exc.code == 4014:
                        _log.info(
                            "Disconnected from voice by force... potentially reconnecting."
                        )
                        successful = await self.potential_reconnect()
                        if not successful:
                            _log.info(
                                "Reconnect was unsuccessful, disconnecting from voice normally..."
                            )
                            await self.disconnect()
                            break
                        continue

                if not reconnect:
                    await self.disconnect()
                    raise

                retry = backoff.delay()
                _log.exception(
                    "Disconnected from voice... Reconnecting in %.2fs.", retry
                )
                self._connected.clear()
                await asyncio.sleep(retry)
                await self.voice_disconnect()
                try:
                    await self.connect(reconnect=True, timeout=self.timeout)
                except asyncio.TimeoutError:
                    # at this point we've retried 5 times... let's continue the loop.
                    _log.warning("Could not connect to voice... Retrying...")
                    continue

    VoiceClient.on_voice_server_update = patched_on_voice_server_update
    VoiceClient.poll_voice_ws = patched_poll_voice_ws
