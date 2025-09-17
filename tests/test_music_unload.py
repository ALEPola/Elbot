import asyncio

import asyncio

import nextcord
from nextcord.ext import commands

from cogs import music as music_cog


class DummyManager:
    def __init__(self, bot):
        self.bot = bot
        self.closed = False

    async def wait_ready(self, timeout=None):  # pragma: no cover - unused
        return True

    async def close(self):
        self.closed = True

    def handle_node_ready(self, node):  # pragma: no cover - unused
        return None

    def handle_node_unavailable(self, node):  # pragma: no cover - unused
        return None

    async def resolve(self, *args, **kwargs):  # pragma: no cover - unused
        raise NotImplementedError


def test_music_cog_unload_disconnects_voice(monkeypatch):
    monkeypatch.setattr(music_cog, "LavalinkManager", DummyManager)

    class DummyPlayer:
        def __init__(self):
            self.disconnected = False

        async def disconnect(self, *, force=False):
            self.disconnected = True

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    intents = nextcord.Intents.none()
    bot = commands.Bot(command_prefix="!", intents=intents, loop=loop)
    cog = music_cog.Music(bot)

    player = DummyPlayer()
    guild = type("Guild", (), {"id": 1, "voice_client": player})()
    monkeypatch.setattr(commands.Bot, "guilds", property(lambda self: [guild]))

    state = music_cog.MusicState(guild_id=1, player=player)
    cog._states[1] = state

    loop.run_until_complete(cog.cog_unload())

    assert cog.manager.closed
    assert player.disconnected

    loop.close()
