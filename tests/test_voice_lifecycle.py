import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import nextcord
import pytest

from elbot.cogs.ai import AICog
from elbot.cogs.music import GuildState, Music


def make_music():
    music = Music.__new__(Music)
    music.logger = logging.getLogger("test.voice")
    music._states = {7: GuildState()}
    return music


def make_channel(error=None):
    registry = {}
    state = SimpleNamespace(
        _get_voice_client=registry.get,
        _add_voice_client=lambda key, player: registry.update({key: player}),
        _remove_voice_client=lambda key: registry.pop(key, None),
    )
    client = SimpleNamespace(_connection=state)
    state._get_client = lambda: client
    channel = SimpleNamespace(id=8, guild=SimpleNamespace(id=7), _state=state)
    channel._get_voice_client_key = lambda: (7, "guild_id")
    # Exercise Nextcord's actual registration and exception handling.
    channel.connect = lambda **kwargs: nextcord.abc.Connectable.connect(channel, **kwargs)

    class Player(nextcord.VoiceProtocol):
        def __init__(self, client, channel):
            super().__init__(client, channel)
            self.connected = False
            self.guild = channel.guild
            self.disconnect = AsyncMock(side_effect=self.release)

        async def connect(self, **kwargs):
            if error == "hang":
                await asyncio.Event().wait()
            if error:
                raise error
            self.connected = True

        async def release(self, **kwargs):
            self.connected = False
            self.cleanup()

    return channel, Player, registry


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [RuntimeError("gateway failed"), asyncio.TimeoutError(), "hang"])
async def test_failed_join_releases_registration_and_allows_retry(error):
    music = make_music()
    channel, player_cls, registry = make_channel(error)
    with pytest.raises((RuntimeError, asyncio.TimeoutError)):
        await music._connect_voice(channel, player_cls, 0.01)
    assert registry == {}

    async def connected(self, **kwargs):
        self.connected = True

    player_cls.connect = connected
    player = await music._connect_voice(channel, player_cls, 1)
    assert registry[7] is player
    await music._release_voice(player)
    assert registry == {}


@pytest.mark.asyncio
async def test_cancelled_join_releases_registration():
    music = make_music()
    channel, player_cls, registry = make_channel("hang")
    task = asyncio.create_task(music._connect_voice(channel, player_cls, 10))
    while not registry:
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert registry == {}


@pytest.mark.asyncio
async def test_failed_disconnect_still_cleans_local_client():
    music = make_music()
    channel, player_cls, registry = make_channel()
    player = await music._connect_voice(channel, player_cls, 1)
    player.disconnect.side_effect = RuntimeError("Lavalink unavailable")
    await music._release_voice(player)
    assert registry == {}


@pytest.mark.asyncio
async def test_reconnect_preserves_queue_and_replaces_player():
    music = make_music()
    channel, player_cls, registry = make_channel()
    state = music._states[7]
    state.player = await music._connect_voice(channel, player_cls, 1)
    original = state.player
    queued = object()
    state.queue.add(queued)
    replacement = await music._reconnect_player(7, state, original, SimpleNamespace(Player=player_cls), 1)
    assert replacement is not original
    assert registry[7] is state.player is replacement
    assert state.queue.snapshot() == [queued]
    await music._release_voice(replacement)


@pytest.mark.asyncio
async def test_parallel_join_requests_are_serialized():
    music = make_music()
    active = 0
    peak = 0

    async def ensure(interaction):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1
        return None, None

    music._ensure_voice_locked = ensure
    interaction = SimpleNamespace(guild=SimpleNamespace(id=7))
    await asyncio.gather(music._ensure_voice(interaction), music._ensure_voice(interaction))
    assert peak == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", [False, True])
async def test_ai_voice_placeholder_does_not_touch_voice(existing):
    cog = AICog.__new__(AICog)
    cog._disabled_voice_guilds = set()
    player = SimpleNamespace(move_to=AsyncMock(), disconnect=AsyncMock())
    channel = SimpleNamespace(connect=AsyncMock())
    interaction = SimpleNamespace(
        guild_id=7,
        guild=SimpleNamespace(voice_client=player if existing else None),
        user=SimpleNamespace(voice=SimpleNamespace(channel=channel)),
        response=SimpleNamespace(send_message=AsyncMock()),
    )
    await cog.ai_voice(interaction)
    interaction.response.send_message.assert_awaited_once()
    player.move_to.assert_not_awaited()
    player.disconnect.assert_not_awaited()
    channel.connect.assert_not_awaited()
