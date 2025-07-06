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
    cog.node = object()

    vc = DummyVoiceClient()
    dummy_guild = type("Guild", (), {"voice_client": vc})()
    guilds = [dummy_guild]

    monkeypatch.setattr(commands.Bot, "guilds", property(lambda self: guilds))

    closed = False

    async def fake_close():
        nonlocal closed
        closed = True

    monkeypatch.setattr(music_cog.wavelink.Pool, "close", fake_close)

    loop.run_until_complete(cog.cog_unload())

    assert closed
    assert vc.disconnected
    loop.close()
