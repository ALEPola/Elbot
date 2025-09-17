import asyncio

import nextcord
from nextcord.ext import commands

from cogs import music as music_cog


class DummyManager:
    def __init__(self, bot):
        self.bot = bot
        self.ready = True
        self.closed = False

    async def wait_ready(self, timeout=None):
        return self.ready

    async def close(self):
        self.closed = True

    async def resolve(self, *args, **kwargs):  # pragma: no cover - not used here
        raise NotImplementedError

    def handle_node_ready(self, node):  # pragma: no cover - not used here
        self.ready = True

    def handle_node_unavailable(self, node):  # pragma: no cover - not used here
        self.ready = False


def test_ensure_voice_connects_with_mafic_player(monkeypatch):
    monkeypatch.setattr(music_cog, "LavalinkManager", DummyManager)

    recorded = {}

    class DummyPlayer:
        def __init__(self, client, channel):
            self.client = client
            self.channel = channel
            self.disconnected = False

        async def disconnect(self, *, force=False):
            self.disconnected = True

    monkeypatch.setattr(music_cog.mafic, "Player", DummyPlayer)

    class DummyChannel:
        def __init__(self, guild):
            self.guild = guild

        async def connect(self, *, cls):
            recorded["cls"] = cls
            player = cls(bot, self)
            self.guild.voice_client = player
            return player

    guild = type("Guild", (), {"id": 42, "voice_client": None})()
    channel = DummyChannel(guild)
    user = type("User", (), {"voice": type("VS", (), {"channel": channel})()})()
    interaction = type(
        "Interaction",
        (),
        {
            "user": user,
            "guild": guild,
        },
    )()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    intents = nextcord.Intents.none()
    bot = commands.Bot(command_prefix="!", intents=intents, loop=loop)
    cog = music_cog.Music(bot)

    player, message = loop.run_until_complete(cog.ensure_voice(interaction))
    assert message is None
    assert isinstance(player, DummyPlayer)
    assert recorded["cls"] is DummyPlayer

    loop.run_until_complete(cog.manager.close())
    loop.close()


def test_ensure_voice_errors_when_node_unready(monkeypatch):
    class SlowManager(DummyManager):
        async def wait_ready(self, timeout=None):
            return False

    monkeypatch.setattr(music_cog, "LavalinkManager", SlowManager)
    monkeypatch.setattr(music_cog.mafic, "Player", object)

    guild = type("Guild", (), {"id": 99, "voice_client": None})()
    channel = type("Chan", (), {"guild": guild, "connect": lambda *a, **k: None})()
    user = type("User", (), {"voice": type("VS", (), {"channel": channel})()})()
    interaction = type("Interaction", (), {"user": user, "guild": guild})()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    intents = nextcord.Intents.none()
    bot = commands.Bot(command_prefix="!", intents=intents, loop=loop)
    cog = music_cog.Music(bot)

    player, message = loop.run_until_complete(cog.ensure_voice(interaction))
    assert player is None
    assert "not ready" in message.lower()

    loop.run_until_complete(cog.manager.close())
    loop.close()
