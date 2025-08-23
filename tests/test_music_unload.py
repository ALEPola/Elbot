import asyncio

import nextcord
from nextcord.ext import commands

from cogs import music as music_cog


class DummyVoiceClient:
    def __init__(self):
        self.disconnected = False

    async def disconnect(self):
        self.disconnected = True


def test_music_cog_unload_disconnects_voice(monkeypatch):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    intents = nextcord.Intents.none()
    bot = commands.Bot(command_prefix="!", intents=intents, loop=loop)
    cog = music_cog.Music(bot)

    vc = DummyVoiceClient()
    dummy_guild = type("Guild", (), {"voice_client": vc})()
    guilds = [dummy_guild]

    monkeypatch.setattr(commands.Bot, "guilds", property(lambda self: guilds))

    async def fake_wait_until_ready(self):
        return None

    monkeypatch.setattr(commands.Bot, "wait_until_ready", fake_wait_until_ready)
    monkeypatch.setattr(music_cog.wavelink.Pool, 'is_connected', lambda: False, raising=False)

    closed = {"closed": False}

    class DummyNode:
        status = music_cog.wavelink.NodeStatus.CONNECTED

        async def close(self):
            closed["closed"] = True

    async def fake_connect(*, nodes, client=None, cache_capacity=None):
        return {"MAIN": DummyNode()}

    monkeypatch.setattr(music_cog.wavelink.Pool, "connect", fake_connect)

    loop.run_until_complete(cog.connect_task)

    loop.run_until_complete(cog.cog_unload())

    assert closed["closed"]
    assert vc.disconnected
    loop.close()
