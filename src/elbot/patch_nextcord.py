"""
Patch nextcord to avoid AttributeError in VoiceClient.poll_voice_ws.
Nextcord assumes websocket objects expose poll_event, which was removed in websockets>=12.
This guard mirrors discord.py's fix for similar issues (see nextcord/nextcord#1262
and Rapptz/discord.py#10207).
"""
import asyncio

from nextcord.voice_client import (
    VoiceClient,
    ExponentialBackoff,
    ConnectionClosed,
    _log,
)


def apply_patch() -> None:
    async def patched_poll_voice_ws(self: VoiceClient, reconnect: bool) -> None:
        backoff = ExponentialBackoff()
        while True:
            try:
                if self.ws and hasattr(self.ws, "poll_event"):
                    await self.ws.poll_event()
                else:
                    # Yield to the event loop when there is no active websocket.
                    # Otherwise this loop busy-waits and starves the heartbeat task.
                    await asyncio.sleep(0.1)
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

    VoiceClient.poll_voice_ws = patched_poll_voice_ws
